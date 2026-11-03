// A QR code encoder for the pairing URL: byte mode, error correction level M, the smallest
// version from 1 to 40 that holds the text, and the mask with the lowest penalty. It needs
// no document; the modules view draws its result as an SVG path.

/** A QR code: `size` modules a side, `dark[y][x]` true for a dark module. */
export interface QrCode {
  version: number;
  mask: number;
  size: number;
  dark: boolean[][];
}

// Level M, by version (index 0 unused): error correction codewords per block, and blocks.
const ECC_PER_BLOCK = [-1, 10, 16, 26, 18, 24, 16, 18, 22, 22, 26, 30, 22, 22, 24, 24, 28, 28, 26,
  26, 26, 26, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28];
const BLOCKS = [-1, 1, 1, 1, 2, 2, 4, 4, 4, 5, 5, 5, 8, 9, 9, 10, 10, 11, 13, 14, 16, 17, 17, 18,
  20, 21, 23, 25, 26, 28, 29, 31, 33, 35, 37, 38, 40, 43, 45, 47, 49];
// Level M's two format bits.
const LEVEL_M = 0;

function bit(value: number, index: number): boolean {
  return ((value >>> index) & 1) !== 0;
}

/** The modules a version has for data and error correction, after the function patterns. */
export function rawModules(version: number): number {
  let result = (16 * version + 128) * version + 64;
  if (version >= 2) {
    const align = Math.floor(version / 7) + 2;
    result -= (25 * align - 10) * align - 55;
    if (version >= 7) result -= 36;
  }
  return result;
}

/** The data codewords a version holds at level M. */
export function dataCodewords(version: number): number {
  return Math.floor(rawModules(version) / 8) - ECC_PER_BLOCK[version] * BLOCKS[version];
}

/** The centres of a version's alignment patterns, on each axis. */
export function alignment(version: number): number[] {
  if (version === 1) return [];
  const count = Math.floor(version / 7) + 2;
  const step = Math.floor((version * 8 + count * 3 + 5) / (count * 4 - 4)) * 2;
  const result = [6];
  for (let pos = version * 4 + 10; result.length < count; pos -= step) result.splice(1, 0, pos);
  return result;
}

// --- Reed-Solomon over GF(256), polynomial 0x11D ------------------------------------------

function multiply(x: number, y: number): number {
  let z = 0;
  for (let i = 7; i >= 0; i--) {
    z = (z << 1) ^ ((z >>> 7) * 0x11d);
    z ^= ((y >>> i) & 1) * x;
  }
  return z;
}

function divisor(degree: number): number[] {
  const result = new Array<number>(degree).fill(0);
  result[degree - 1] = 1;
  let root = 1;
  for (let i = 0; i < degree; i++) {
    for (let j = 0; j < result.length; j++) {
      result[j] = multiply(result[j], root);
      if (j + 1 < result.length) result[j] ^= result[j + 1];
    }
    root = multiply(root, 0x02);
  }
  return result;
}

function remainder(data: readonly number[], by: readonly number[]): number[] {
  const result = new Array<number>(by.length).fill(0);
  for (const byte of data) {
    const factor = byte ^ (result.shift() as number);
    result.push(0);
    by.forEach((coef, i) => { result[i] ^= multiply(coef, factor); });
  }
  return result;
}

/** The data codewords split into blocks, each followed by its error correction, interleaved. */
function withEcc(data: readonly number[], version: number): number[] {
  const blocks = BLOCKS[version];
  const eccLen = ECC_PER_BLOCK[version];
  const raw = Math.floor(rawModules(version) / 8);
  const short = blocks - (raw % blocks);
  const shortLen = Math.floor(raw / blocks);
  const by = divisor(eccLen);
  const parts: number[][] = [];
  for (let i = 0, k = 0; i < blocks; i++) {
    const block = data.slice(k, k + shortLen - eccLen + (i < short ? 0 : 1));
    k += block.length;
    const ecc = remainder(block, by);
    if (i < short) block.push(0);
    parts.push(block.concat(ecc));
  }
  const result: number[] = [];
  for (let i = 0; i < parts[0].length; i++) {
    parts.forEach((part, j) => {
      if (i !== shortLen - eccLen || j >= short) result.push(part[i]);
    });
  }
  return result;
}

// --- the data codewords --------------------------------------------------------------------

/** The data codewords of `bytes` in byte mode for `version`, padded to its capacity, or
 * null when they don't fit. */
export function codewords(bytes: Uint8Array, version: number): number[] | null {
  const capacity = dataCodewords(version) * 8;
  const bits: number[] = [];
  const put = (value: number, length: number) => {
    for (let i = length - 1; i >= 0; i--) bits.push((value >>> i) & 1);
  };
  const countBits = version <= 9 ? 8 : 16;
  if (bytes.length >= 2 ** countBits) return null;
  put(0b0100, 4);
  put(bytes.length, countBits);
  for (const byte of bytes) put(byte, 8);
  if (bits.length > capacity) return null;
  put(0, Math.min(4, capacity - bits.length));
  put(0, (8 - (bits.length % 8)) % 8);
  const result: number[] = [];
  for (let i = 0; i < bits.length; i += 8) result.push(bits.slice(i, i + 8).reduce((a, b) => (a << 1) | b, 0));
  for (let pad = 0xec; result.length < capacity / 8; pad ^= 0xec ^ 0x11) result.push(pad);
  return result;
}

// --- the matrix ----------------------------------------------------------------------------

class Matrix {
  readonly size: number;
  readonly dark: boolean[][];
  readonly fixed: boolean[][];
  readonly version: number;

  constructor(version: number) {
    this.version = version;
    this.size = version * 4 + 17;
    this.dark = Array.from({ length: this.size }, () => new Array<boolean>(this.size).fill(false));
    this.fixed = Array.from({ length: this.size }, () => new Array<boolean>(this.size).fill(false));
  }

  set(x: number, y: number, dark: boolean): void {
    this.dark[y][x] = dark;
    this.fixed[y][x] = true;
  }

  patterns(): void {
    const { size } = this;
    for (let i = 0; i < size; i++) {
      this.set(6, i, i % 2 === 0);
      this.set(i, 6, i % 2 === 0);
    }
    for (const [cx, cy] of [[3, 3], [size - 4, 3], [3, size - 4]]) {
      for (let dy = -4; dy <= 4; dy++) {
        for (let dx = -4; dx <= 4; dx++) {
          const x = cx + dx;
          const y = cy + dy;
          const dist = Math.max(Math.abs(dx), Math.abs(dy));
          if (x >= 0 && x < size && y >= 0 && y < size) this.set(x, y, dist !== 2 && dist !== 4);
        }
      }
    }
    const centres = alignment(this.version);
    const last = centres.length - 1;
    centres.forEach((cx, i) => centres.forEach((cy, j) => {
      if ((i === 0 && j === 0) || (i === 0 && j === last) || (i === last && j === 0)) return;
      for (let dy = -2; dy <= 2; dy++) {
        for (let dx = -2; dx <= 2; dx++) this.set(cx + dx, cy + dy, Math.max(Math.abs(dx), Math.abs(dy)) !== 1);
      }
    }));
    this.format(0);
    if (this.version >= 7) {
      let rem = this.version;
      for (let i = 0; i < 12; i++) rem = (rem << 1) ^ ((rem >>> 11) * 0x1f25);
      const bits = (this.version << 12) | rem;
      for (let i = 0; i < 18; i++) {
        const a = size - 11 + (i % 3);
        const b = Math.floor(i / 3);
        this.set(a, b, bit(bits, i));
        this.set(b, a, bit(bits, i));
      }
    }
  }

  /** The format bits for level M and `mask`, in both copies, and the dark module. */
  format(mask: number): void {
    const data = (LEVEL_M << 3) | mask;
    let rem = data;
    for (let i = 0; i < 10; i++) rem = (rem << 1) ^ ((rem >>> 9) * 0x537);
    const bits = ((data << 10) | rem) ^ 0x5412;
    const { size } = this;
    for (let i = 0; i <= 5; i++) this.set(8, i, bit(bits, i));
    this.set(8, 7, bit(bits, 6));
    this.set(8, 8, bit(bits, 7));
    this.set(7, 8, bit(bits, 8));
    for (let i = 9; i < 15; i++) this.set(14 - i, 8, bit(bits, i));
    for (let i = 0; i < 8; i++) this.set(size - 1 - i, 8, bit(bits, i));
    for (let i = 8; i < 15; i++) this.set(8, size - 15 + i, bit(bits, i));
    this.set(8, size - 8, true);
  }

  place(data: readonly number[]): void {
    const { size } = this;
    let i = 0;
    for (let right = size - 1; right >= 1; right -= 2) {
      if (right === 6) right = 5;
      for (let vert = 0; vert < size; vert++) {
        for (let j = 0; j < 2; j++) {
          const x = right - j;
          const y = ((right + 1) & 2) === 0 ? size - 1 - vert : vert;
          if (!this.fixed[y][x] && i < data.length * 8) {
            this.dark[y][x] = bit(data[i >>> 3], 7 - (i & 7));
            i++;
          }
        }
      }
    }
  }

  mask(mask: number): void {
    for (let y = 0; y < this.size; y++) {
      for (let x = 0; x < this.size; x++) {
        if (!this.fixed[y][x] && MASKS[mask](x, y)) this.dark[y][x] = !this.dark[y][x];
      }
    }
  }
}

const MASKS: ReadonlyArray<(x: number, y: number) => boolean> = [
  (x, y) => (x + y) % 2 === 0,
  (_x, y) => y % 2 === 0,
  (x) => x % 3 === 0,
  (x, y) => (x + y) % 3 === 0,
  (x, y) => (Math.floor(x / 3) + Math.floor(y / 2)) % 2 === 0,
  (x, y) => ((x * y) % 2) + ((x * y) % 3) === 0,
  (x, y) => (((x * y) % 2) + ((x * y) % 3)) % 2 === 0,
  (x, y) => (((x + y) % 2) + ((x * y) % 3)) % 2 === 0,
];

/** The penalty of a finished matrix: runs of five or more, 2x2 blocks, finder-like
 * stretches and the dark balance. Any mask reads; the lowest penalty reads best. */
export function penalty(dark: readonly boolean[][]): number {
  const size = dark.length;
  let score = 0;
  const lines: boolean[][] = [];
  for (let i = 0; i < size; i++) {
    lines.push(dark[i]);
    lines.push(dark.map((row) => row[i]));
  }
  const finder = [true, false, true, true, true, false, true];
  for (const line of lines) {
    for (let start = 0; start < size;) {
      let end = start;
      while (end < size && line[end] === line[start]) end++;
      if (end - start >= 5) score += end - start - 2;
      start = end;
    }
    for (let at = 0; at + 7 <= size; at++) {
      if (!finder.every((value, k) => line[at + k] === value)) continue;
      const lightRun = (from: number, step: number) => {
        for (let k = 1; k <= 4; k++) {
          const pos = from + step * k;
          if (pos >= 0 && pos < size && line[pos]) return false;
        }
        return true;
      };
      if (lightRun(at, -1)) score += 40;
      if (lightRun(at + 6, 1)) score += 40;
    }
  }
  for (let y = 0; y + 1 < size; y++) {
    for (let x = 0; x + 1 < size; x++) {
      const value = dark[y][x];
      if (value === dark[y][x + 1] && value === dark[y + 1][x] && value === dark[y + 1][x + 1]) score += 3;
    }
  }
  const total = size * size;
  const count = dark.reduce((sum, row) => sum + row.filter(Boolean).length, 0);
  score += Math.max(0, Math.ceil(Math.abs(count * 20 - total * 10) / total) - 1) * 10;
  return score;
}

/** The QR code of `text` (UTF-8). `mask` forces one of the eight masks; without it the
 * lowest penalty wins. Throws a RangeError for a text too long for version 40. */
export function encode(text: string, mask?: number): QrCode {
  const bytes = new TextEncoder().encode(text);
  for (let version = 1; version <= 40; version++) {
    const data = codewords(bytes, version);
    if (data === null) continue;
    const all = withEcc(data, version);
    const masks = mask === undefined ? [0, 1, 2, 3, 4, 5, 6, 7] : [mask];
    let best: { mask: number; score: number; dark: boolean[][] } | null = null;
    for (const chosen of masks) {
      const matrix = new Matrix(version);
      matrix.patterns();
      matrix.place(all);
      matrix.mask(chosen);
      matrix.format(chosen);
      const score = masks.length === 1 ? 0 : penalty(matrix.dark);
      if (best === null || score < best.score) best = { mask: chosen, score, dark: matrix.dark };
    }
    const found = best as { mask: number; dark: boolean[][] };
    return { version, mask: found.mask, size: version * 4 + 17, dark: found.dark };
  }
  throw new RangeError("the text is too long for a QR code");
}

/** An SVG path of the dark modules, one unit each, for a viewBox that starts at 0. */
export function svgPath(code: QrCode): string {
  const parts: string[] = [];
  code.dark.forEach((row, y) => row.forEach((on, x) => {
    if (on) parts.push(`M${x} ${y}h1v1h-1z`);
  }));
  return parts.join("");
}
