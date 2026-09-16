import sys
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple


def split_blocks(text: str) -> List[str]:
    """
    Split ontology text into top-level Turtle blocks ending with '.'.
    Keeps comments and formatting.
    """
    lines = text.splitlines(keepends=True)
    blocks = []
    current = []

    for line in lines:
        current.append(line)
        if re.search(r'\s\.\s*$', line):
            blocks.append("".join(current))
            current = []

    if current:
        blocks.append("".join(current))

    return blocks


def is_object_property_block(block: str) -> bool:
    return "rdf:type owl:ObjectProperty" in block


def extract_subject(block: str) -> Optional[str]:
    for line in block.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("###"):
            continue
        m = re.match(r'^(\S+)\s+rdf:type\s+owl:ObjectProperty\s*;', stripped)
        if m:
            return m.group(1)
    return None


def get_predicate_indent(block: str) -> str:
    for line in block.splitlines():
        m = re.match(r'^(\s+)\S', line)
        if m:
            return m.group(1)
    return "    "


def extract_multiline_predicate_objects(block: str, predicate: str) -> List[str]:
    """
    Extract full object expressions of a predicate, including multiline values.

    Examples returned:
      lis:UnitOfMeasure
      [ rdf:type owl:Class ;
        owl:unionOf ( lis:A lis:B )
      ]
    """
    lines = block.splitlines(keepends=False)
    results = []

    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        if stripped.startswith(predicate + " "):
            obj_part = stripped[len(predicate):].strip()

            collected = [obj_part]

            bracket_balance = obj_part.count("[") - obj_part.count("]")
            paren_balance = obj_part.count("(") - obj_part.count(")")

            # If current line already finishes the statement and balances are closed
            while True:
                end_char = collected[-1].rstrip()[-1] if collected[-1].rstrip() else ""
                if bracket_balance <= 0 and paren_balance <= 0 and end_char in [";", "."]:
                    break

                i += 1
                if i >= len(lines):
                    break

                next_line = lines[i]
                collected.append(next_line.strip())

                bracket_balance += next_line.count("[") - next_line.count("]")
                paren_balance += next_line.count("(") - next_line.count(")")

            full_value = "\n".join(collected).strip()

            # Remove final ; or .
            full_value = re.sub(r'[;.]$', '', full_value).rstrip()
            results.append(full_value)

        i += 1

    return results


def extract_first_object(block: str, predicate: str) -> Optional[str]:
    values = extract_multiline_predicate_objects(block, predicate)
    return values[0] if values else None


def has_predicate(block: str, predicate: str) -> bool:
    pattern = rf'^\s*{re.escape(predicate)}\s+'
    return any(re.match(pattern, line) for line in block.splitlines())


def build_property_index(blocks: List[str]) -> Dict[str, Dict[str, object]]:
    index = {}

    for block in blocks:
        if not is_object_property_block(block):
            continue

        subject = extract_subject(block)
        if not subject:
            continue

        index[subject] = {
            "block": block,
            "domain": extract_multiline_predicate_objects(block, "rdfs:domain"),
            "range": extract_multiline_predicate_objects(block, "rdfs:range"),
            "inverse": extract_first_object(block, "owl:inverseOf"),
        }

    return index


def format_inserted_predicate(indent: str, predicate: str, obj: str) -> List[str]:
    """
    Format inserted predicate preserving multiline object values.

    Example output:
       rdfs:range [ rdf:type owl:Class ;
                    owl:unionOf ( lis:A
                                  lis:B
                                )
                  ] ;
    """
    obj_lines = obj.splitlines()

    if len(obj_lines) == 1:
        return [f"{indent}{predicate} {obj_lines[0]} ;\n"]

    formatted = [f"{indent}{predicate} {obj_lines[0]}\n"]
    continuation_indent = " " * (len(indent) + len(predicate) + 1)

    for idx, line in enumerate(obj_lines[1:], start=1):
        if idx == len(obj_lines) - 1:
            formatted.append(f"{continuation_indent}{line} ;\n")
        else:
            formatted.append(f"{continuation_indent}{line}\n")

    return formatted


def insert_domain_range(block: str, domains_to_add: List[str], ranges_to_add: List[str]) -> str:
    lines = block.splitlines(keepends=True)
    indent = get_predicate_indent(block)

    insert_lines: List[str] = []

    for d in domains_to_add:
        insert_lines.extend(format_inserted_predicate(indent, "rdfs:domain", d))

    for r in ranges_to_add:
        insert_lines.extend(format_inserted_predicate(indent, "rdfs:range", r))

    if not insert_lines:
        return block

    insert_at = None

    # Prefer after last rdfs:subPropertyOf
    for i, line in enumerate(lines):
        if re.search(r'\brdfs:subPropertyOf\b', line):
            insert_at = i + 1

    # Otherwise after rdf:type owl:ObjectProperty
    if insert_at is None:
        for i, line in enumerate(lines):
            if re.search(r'\brdf:type\s+owl:ObjectProperty\b', line):
                insert_at = i + 1
                break

    if insert_at is None:
        return block

    new_lines = lines[:insert_at] + insert_lines + lines[insert_at:]
    return "".join(new_lines)


def enrich_object_properties_with_inverse_domain_range(input_file: str, output_file: str) -> None:
    text = Path(input_file).read_text(encoding="utf-8")
    blocks = split_blocks(text)
    index = build_property_index(blocks)

    updated_blocks = []
    total_added = 0

    for block in blocks:
        if not is_object_property_block(block):
            updated_blocks.append(block)
            continue

        subject = extract_subject(block)
        if not subject or subject not in index:
            updated_blocks.append(block)
            continue

        prop_info = index[subject]
        inverse_prop = prop_info["inverse"]

        if not inverse_prop or inverse_prop not in index:
            updated_blocks.append(block)
            continue

        inverse_info = index[inverse_prop]

        existing_domains = set(prop_info["domain"])
        existing_ranges = set(prop_info["range"])

        inverse_domains = inverse_info["domain"]
        inverse_ranges = inverse_info["range"]

        domains_to_add = []
        ranges_to_add = []

        # inverse range -> current domain
        if not existing_domains:
            for inv_range in inverse_ranges:
                if inv_range not in existing_domains:
                    domains_to_add.append(inv_range)

        # inverse domain -> current range
        if not existing_ranges:
            for inv_domain in inverse_domains:
                if inv_domain not in existing_ranges:
                    ranges_to_add.append(inv_domain)

        new_block = insert_domain_range(block, domains_to_add, ranges_to_add)
        total_added += len(domains_to_add) + len(ranges_to_add)
        updated_blocks.append(new_block)

    Path(output_file).write_text("".join(updated_blocks), encoding="utf-8")
    print(f"Done. Added {total_added} statements.")
    print(f"Output written to: {output_file}")


def main():
    if len(sys.argv) < 3:
        print("Usage: python add_domain_range_to_inverse_properties.py input.ttl output.ttl")
        sys.exit(1)
    src = Path(sys.argv[1])
    dst = Path(sys.argv[2])
    enrich_object_properties_with_inverse_domain_range(
    input_file=src,
    output_file=dst
    )

if __name__ == "__main__":
    main()


