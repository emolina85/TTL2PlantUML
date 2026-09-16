
import re
import sys
from pathlib import Path

BLOCK_RE = re.compile(
    r'(?ms)^(?P<subject>\S+)\s+rdf:type\s+owl:(?P<kind>ObjectProperty|DatatypeProperty)\s*;\n'
    r'(?P<body>.*?)(?=^\s*$|\Z)'
)

def convert_match(m: re.Match) -> str:
    subject = m.group("subject")
    kind = m.group("kind")
    body = m.group("body").rstrip("\n")

    extra_types = []
    remaining = []
    for line in body.splitlines():
        stripped = line.strip()
        mt = re.match(r'^rdf:type\s+([^;]+)\s*;\s*$', stripped)
        if mt:
            extra_types.append(mt.group(1).strip())
        else:
            remaining.append(stripped)

    out = [f"{subject}", "  a rdf:Property ;", f"  a owl:{kind} ;"]
    for t in extra_types:
        out.append(f"  a {t} ;")

    # normalize final predicate
    cleaned = [x for x in remaining if x != ""]
    for i, line in enumerate(cleaned):
        if line == ".":
            continue
        if i == len(cleaned) - 1:
            if line.endswith(" ."):
                line = line[:-2] + " ;"
            elif line.endswith("."):
                line = line[:-1] + " ;"
            elif not line.endswith(";"):
                line = line + " ;"
        out.append("  " + line)
    out.append(".")
    return "\n".join(out)

def convert_ttl(text: str) -> str:
    return BLOCK_RE.sub(convert_match, text)

def convert_ttl_style(input_file: str, output_file: str):
    src = Path(input_file)
    dst = Path(output_file)
    dst.write_text(convert_ttl(src.read_text(encoding="utf-8")), encoding="utf-8")
    print(f"Written: {dst}")

def main():
    if len(sys.argv) < 3:
        print("Usage: python convert_lis_to_w3c_style.py input.ttl output.ttl")
        sys.exit(1)
    src = Path(sys.argv[1])
    dst = Path(sys.argv[2])
    convert_ttl_style(src, dst)
    # dst.write_text(convert_ttl(src.read_text(encoding="utf-8")), encoding="utf-8")
    # print(f"Written: {dst}")

if __name__ == "__main__":
    main()
