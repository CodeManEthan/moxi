// re2_samples: Go's matches on samples generated from each pattern's parse tree.
//
// Dev-only, run by bump_gitleaks.py. It reads a JSON request on stdin,
//
//	{"samples": 60, "seed": 7, "patterns": [{"id": "...", "pattern": "...", "group": 0}]}
//
// and for each pattern makes `samples` texts from the pattern's own parse tree, each in a
// fixed list of variants (case flips, quoting, line endings, Unicode case folds, non-ASCII
// neighbours). For each text it writes one JSON line to stdout,
//
//	{"id": "...", "kind": "plain", "text": "...", "found": true, "matches": [[ms, me, ss, se]]}
//
// where `found` is MatchString, and `matches` lists every non-empty match of
// FindAllStringSubmatchIndex with its secret, as gitleaks takes it: the group `group` names
// when it is above zero, else the first non-empty group, else the whole match. A secret
// group the match has no span for is [-1, -1]. Offsets are UTF-8 byte offsets.
//
// Nothing is stored: the caller reads the lines as they come and keeps none.
package main

import (
	"bufio"
	"encoding/json"
	"fmt"
	"io"
	"math/rand"
	"os"
	"regexp"
	"regexp/syntax"
	"strings"
	"unicode"
)

type pattern struct {
	ID      string `json:"id"`
	Pattern string `json:"pattern"`
	Group   int    `json:"group"`
}

type request struct {
	Samples  int       `json:"samples"`
	Seed     int64     `json:"seed"`
	Patterns []pattern `json:"patterns"`
}

type sample struct {
	ID      string  `json:"id"`
	Kind    string  `json:"kind"`
	Text    string  `json:"text"`
	Found   bool    `json:"found"`
	Matches [][]int `json:"matches"`
}

var rng *rand.Rand

// pickRune picks a rune from a character class, printable ASCII when the class has any.
func pickRune(ranges []rune) rune {
	var ascii [][2]rune
	for i := 0; i+1 < len(ranges); i += 2 {
		lo, hi := ranges[i], ranges[i+1]
		if lo < 0x21 {
			lo = 0x21
		}
		if hi > 0x7e {
			hi = 0x7e
		}
		if lo <= hi {
			ascii = append(ascii, [2]rune{lo, hi})
		}
	}
	if len(ascii) > 0 {
		r := ascii[rng.Intn(len(ascii))]
		return r[0] + rune(rng.Intn(int(r[1]-r[0]+1)))
	}
	if len(ranges) >= 2 {
		lo, hi := ranges[0], ranges[1]
		if hi-lo > 1000 {
			hi = lo + 1000
		}
		return lo + rune(rng.Intn(int(hi-lo+1)))
	}
	return 'x'
}

func flipRune(r rune) rune {
	if unicode.IsUpper(r) {
		return unicode.ToLower(r)
	}
	return unicode.ToUpper(r)
}

// gen writes one string the parse tree matches (anchors and boundaries write nothing).
func gen(re *syntax.Regexp, b *strings.Builder) {
	switch re.Op {
	case syntax.OpLiteral:
		for _, r := range re.Rune {
			if re.Flags&syntax.FoldCase != 0 && rng.Intn(2) == 0 {
				r = flipRune(r)
			}
			b.WriteRune(r)
		}
	case syntax.OpCharClass:
		b.WriteRune(pickRune(re.Rune))
	case syntax.OpAnyChar, syntax.OpAnyCharNotNL:
		b.WriteRune(rune('a' + rng.Intn(26)))
	case syntax.OpCapture:
		gen(re.Sub[0], b)
	case syntax.OpStar, syntax.OpPlus, syntax.OpQuest, syntax.OpRepeat:
		lo, hi := 0, 3
		switch re.Op {
		case syntax.OpPlus:
			lo = 1
		case syntax.OpQuest:
			hi = 1
		case syntax.OpRepeat:
			lo, hi = re.Min, re.Max
			if hi < 0 {
				hi = lo + 3
			}
		}
		n := lo
		if hi > lo {
			n = lo + rng.Intn(hi-lo+1)
		}
		for i := 0; i < n; i++ {
			gen(re.Sub[0], b)
		}
	case syntax.OpConcat:
		for _, s := range re.Sub {
			gen(s, b)
		}
	case syntax.OpAlternate:
		gen(re.Sub[rng.Intn(len(re.Sub))], b)
	}
}

func flipCase(s string) string {
	out := []rune(s)
	for i, r := range out {
		if rng.Intn(3) == 0 {
			out[i] = flipRune(r)
		}
	}
	return string(out)
}

var folds = strings.NewReplacer("k", "K", "s", "ſ")

// variants is the fixed list of texts made from one sample, in a fixed order so a seed
// always gives the same texts.
func variants(s string) [][2]string {
	return [][2]string{
		{"plain", s},
		{"flip", flipCase(s)},
		{"quoted", "token = \"" + s + "\"\n"},
		{"padded", " " + s + " "},
		{"trail-nl", s + "\n"},
		{"vtab", "key = " + s + "\v"},
		{"lead-nl", "x\n" + s},
		{"crlf", "token = " + s + "\r\n"},
		{"fold", folds.Replace(s)},
		{"nbsp", "key = " + s + " rest"},
		{"unicode-pre", "é" + s},
		{"dotless-i", strings.ReplaceAll(s, "i", "ı")},
		{"twice", s + " " + s},
		{"multi", "a\n" + s + "\nb\n" + s},
	}
}

func secret(span []int, group int) (int, int) {
	groups := len(span)/2 - 1
	if groups == 0 {
		return span[0], span[1]
	}
	if group > 0 {
		if group > groups {
			return -1, -1
		}
		return span[2*group], span[2*group+1]
	}
	for g := 1; g <= groups; g++ {
		if span[2*g] >= 0 && span[2*g+1] > span[2*g] {
			return span[2*g], span[2*g+1]
		}
	}
	return span[0], span[1]
}

func run() error {
	data, err := io.ReadAll(os.Stdin)
	if err != nil {
		return err
	}
	var req request
	if err := json.Unmarshal(data, &req); err != nil {
		return err
	}
	rng = rand.New(rand.NewSource(req.Seed))
	out := bufio.NewWriter(os.Stdout)
	defer out.Flush()
	enc := json.NewEncoder(out)
	for _, p := range req.Patterns {
		compiled, err := regexp.Compile(p.Pattern)
		if err != nil {
			return fmt.Errorf("%s: %v", p.ID, err)
		}
		tree, err := syntax.Parse(p.Pattern, syntax.Perl)
		if err != nil {
			return fmt.Errorf("%s: %v", p.ID, err)
		}
		tree = tree.Simplify()
		for i := 0; i < req.Samples; i++ {
			var b strings.Builder
			gen(tree, &b)
			for _, v := range variants(b.String()) {
				matches := [][]int{}
				for _, span := range compiled.FindAllStringSubmatchIndex(v[1], -1) {
					if span[1] == span[0] {
						continue
					}
					ss, se := secret(span, p.Group)
					matches = append(matches, []int{span[0], span[1], ss, se})
				}
				row := sample{p.ID, v[0], v[1], compiled.MatchString(v[1]), matches}
				if err := enc.Encode(row); err != nil {
					return err
				}
			}
		}
	}
	return nil
}

func main() {
	if err := run(); err != nil {
		fmt.Fprintln(os.Stderr, "re2_samples:", err)
		os.Exit(1)
	}
}
