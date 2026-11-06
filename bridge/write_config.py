#!/usr/bin/env python3
"""Render ui/config.json into engine/_config.scd (a SuperCollider literal).

One implementation used by both the bridge (when the UI saves a setting) and
the app launcher (at startup), so the generated file can never drift from the JSON.
"""
import json
import sys


def sc_literal(v):
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(float(v))
    if v is None:
        return "nil"
    if isinstance(v, str):
        return '"' + v.replace("\\", "\\\\").replace('"', '\\"') + '"'
    if isinstance(v, (list, tuple)):
        return "[" + ", ".join(sc_literal(x) for x in v) + "]"
    if isinstance(v, dict):
        return "(" + ", ".join(f"{k}: {sc_literal(x)}" for k, x in v.items()) + ")"
    raise TypeError(f"cannot render {type(v)}")


def render(cfg: dict) -> str:
    return (
        "// generated from ui/config.json -- do not edit\n"
        f"~cfg = {sc_literal(cfg)};\n"
    )


def write_from(json_path: str, scd_path: str) -> dict:
    with open(json_path) as f:
        cfg = json.load(f)
    with open(scd_path, "w") as f:
        f.write(render(cfg))
    return cfg


if __name__ == "__main__":
    write_from(sys.argv[1], sys.argv[2])
