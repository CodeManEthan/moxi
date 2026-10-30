"""Copies of released libraries the core carries, so a module needs none of its own.

`vendor.txt` lists each with its version, and `packages/moxi/tools/vendor.py` makes the
copies from the released wheels, licence files included, with their imports of
themselves pointed here. Nothing imports them at module level: each is imported inside
the function that needs it (`moxi.files.edit_toml` for tomlkit).
"""
