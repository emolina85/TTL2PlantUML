#!/usr/bin/env python3
"""
Pipeline:
1. Read an input TTL file
2. Replace owl:NamedIndividual with owl:Class
3. Call ontogram on the modified TTL
4. Enrich the generated PlantUML (.txt) by adding <<(i,#FF7700)>> to
   classes that use a specific namespace, e.g. "http://example.com/"
5. Write outputs:
   - modified TTL
   - enriched PlantUML txt

Requirements:
    pip install rdflib ontogram

Also ensure the `ontogram` CLI is available in your environment.

Example:
    python ontology_pipeline.py input.ttl --namespace "http://example.com/"
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path
import traceback
from rdflib import Graph, Namespace, RDF, RDFS, OWL
import itertools
from add_domain_range_to_inverse_properties import (
    enrich_object_properties_with_inverse_domain_range,
)

from convert_lis_to_w3c_style import (
    convert_ttl_style,
)

MARKER = "<<(i,#FF7700)>>"
DEFAULT_COLORS = [
    "LightBlue",
    "LightGreen",
    "LightPink",
    "LightYellow",
    "MistyRose",
    "Lavender",
    "PaleTurquoise",
    "Khaki",
    "Thistle",
    "Wheat",
    "Aquamarine",
    "PeachPuff",
]


# Functions to change colors by namespace

PLANTUML_ELEMENT_PATTERN = re.compile(
    r'^(\s*)(Class|class|entity|component|interface)\b(.*)$'
)

PLANTUML_ELEMENT_WITH_URI_PATTERN = re.compile(
    r'^(\s*)(Class|class|entity|component|interface)\s+'
    r'("([^"]+)"|[^\s\[\{]+)'
    r'(.*?)(\[\[([^\]\s]+)(?:\s+[^\]]+)?\]\])?(.*)$'
)


def extract_element_name(rest: str) -> str | None:
    rest = rest.strip()

    quoted_match = re.match(r'^"([^"]+)"', rest)
    if quoted_match:
        return quoted_match.group(1)

    simple_match = re.match(r'^([^\s\{]+)', rest)
    if simple_match:
        return simple_match.group(1)

    return None


def extract_uri_from_line(line: str) -> str | None:
    """
    Extrai a primeira URI do link PlantUML:
      [[http://...#Course]]
      [[http://...#Course :Course]]
    """
    m = re.search(r'\[\[([^\]\s]+)', line)
    if not m:
        return None

    uri = m.group(1).strip()

    if uri.startswith("http://") or uri.startswith("https://"):
        return uri

    return None


def extract_namespace_from_uri(uri: str) -> str:
    """
    Extrai o namespace real de uma URI.

    Exemplos:
      http://x/y#Course -> http://x/y#
      http://x/y/wine#AlsaceRegion -> http://x/y/wine#
      http://x/y/Person -> http://x/y/
    """
    if "#" in uri:
        return uri.rsplit("#", 1)[0] + "#"

    if "/" in uri:
        return uri.rsplit("/", 1)[0] + "/"

    return "default"


def extract_namespace(element_name: str, line: str | None = None) -> str:
    """
    Prioridade:
    1. Namespace real da URI em [[...]]
    2. Namespace do nome do elemento
    3. default
    """
    if line:
        uri = extract_uri_from_line(line)
        if uri:
            return extract_namespace_from_uri(uri)

    if "#" in element_name:
        return element_name.rsplit("#", 1)[0] + "#"

    if "/" in element_name:
        return element_name.rsplit("/", 1)[0] + "/"

    if "." in element_name:
        return element_name.rsplit(".", 1)[0]

    if ":" in element_name and not element_name.startswith(":"):
        return element_name.split(":", 1)[0] + ":"

    return "default"


def add_stereotype_to_line(line: str, stereotype: str) -> str:
    """
    Adiciona <<stereotype>> logo após o nome do elemento.

    Ex:
      Class ":Course" [[http://...#Course]] {
    vira:
      Class ":Course" <<ns0>> [[http://...#Course]] {
    """
    if f'<<{stereotype}>>' in line:
        return line

    line_no_nl = line.rstrip("\n")

    pattern = re.compile(
        r'^(\s*(?:Class|class|entity|component|interface)\s+)'
        r'("([^"]+)"|[^\s\[\{]+)'
        r'(.*)$'
    )

    match = pattern.match(line_no_nl)
    if not match:
        return line

    prefix = match.group(1)
    element_name = match.group(2)
    suffix = match.group(4)

    return f'{prefix}{element_name} <<{stereotype}>>{suffix}\n'


def remove_old_skinparams(lines: list[str]) -> list[str]:
    """
    Remove blocos skinparam class antigos para evitar duplicar cores
    quando a função for executada mais de uma vez.
    """
    new_lines = []
    inside_skinparam_class = False

    for line in lines:
        stripped = line.strip().lower()

        if stripped == "skinparam class {":
            inside_skinparam_class = True
            continue

        if inside_skinparam_class:
            if stripped == "}":
                inside_skinparam_class = False
            continue

        new_lines.append(line)

    return new_lines


def insert_skinparams(lines: list[str], mapping: dict[str, str]) -> list[str]:
    """
    Insere uma cor por estereótipo antes do @enduml.
    """
    lines = remove_old_skinparams(lines)

    block = ["\nskinparam class {\n"]

    for stereotype, color in mapping.items():
        block.append(f"  BackgroundColor<<{stereotype}>> {color}\n")

    block.append("}\n")
    #adding the hide stereotype statement
    block.append("hide stereotype\n")

    for i, line in enumerate(lines):
        if line.strip().lower() == "@enduml":
            return lines[:i] + block + lines[i:]

    return lines + block


def add_color_by_namespace(input_file: str, output_file: str):
    with open(input_file, encoding="utf-8") as f:
        lines = f.readlines()

    namespaces = []

    for line in lines:
        m = PLANTUML_ELEMENT_PATTERN.match(line)
        if not m:
            continue

        _, _, rest = m.groups()

        element_name = extract_element_name(rest)
        if not element_name:
            continue

        ns = extract_namespace(element_name, line)

        if ns not in namespaces:
            namespaces.append(ns)

    color_cycle = itertools.cycle(DEFAULT_COLORS)

    namespace_info = {}

    for i, ns in enumerate(namespaces):
        namespace_info[ns] = {
            "stereotype": f"ns{i}",
            "color": next(color_cycle),
        }

    new_lines = []

    for line in lines:
        m = PLANTUML_ELEMENT_PATTERN.match(line)

        if m:
            _, _, rest = m.groups()

            element_name = extract_element_name(rest)

            if element_name:
                ns = extract_namespace(element_name, line)
                stereotype = namespace_info[ns]["stereotype"]
                line = add_stereotype_to_line(line, stereotype)

        new_lines.append(line)

    stereotype_colors = {
        info["stereotype"]: info["color"]
        for info in namespace_info.values()
    }

    new_lines = insert_skinparams(new_lines, stereotype_colors)

    with open(output_file, "w", encoding="utf-8") as f:
        f.writelines(new_lines)

    return namespace_info


def replace_named_individuals_with_classes(input_ttl: Path, output_ttl: Path) -> None:
    input_ttl = Path(input_ttl).resolve()
    output_ttl = Path(output_ttl).resolve()

    print(f"Input TTL:  {input_ttl}")
    print(f"Output TTL: {output_ttl}")

    if not input_ttl.exists():
        raise FileNotFoundError(f"Input file not found: {input_ttl}")

    output_ttl.parent.mkdir(parents=True, exist_ok=True)

    text = input_ttl.read_text(encoding="utf-8")
    text = text.replace("owl:NamedIndividual", "owl:Class")

    with open(output_ttl, "w", encoding="utf-8", newline="") as f:
        f.write(text)
        f.flush()

    if not output_ttl.exists():
        raise RuntimeError(f"File was not created: {output_ttl}")

    print(f"Saved modified TTL: {output_ttl}")

def transform_ttl_named_individual_blocks(input_ttl: Path, output_ttl: Path) -> list:
    """
    Converts owl:NamedIndividual blocks to owl:Class blocks and creates
    owl:ObjectProperty declarations with rdfs:domain/rdfs:range based on
    relations between transformed classes.
    Accept both sintaxis: 
    :C1 a owl:NamedIndividual
    :C1 rdf:type owl:NamedIndividual ,
             :Course ;
    """

    text = input_ttl.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)

    new_lines = []
    transformed_classes = set()
    object_relations = []

    current_subject = None
    inside_named_individual_block = False

    #ex:8453 a owl:Class ;
    #:C1 rdf:type owl:NamedIndividual ,
    #         :Course ;

    subject_pattern = re.compile(
    r'^([^\s]+)\s+(?:a|rdf:type)\s+owl:NamedIndividual\s*[,;]?' 
    )

    relation_pattern = re.compile(
        r'^\s*([a-zA-Z_][\w\-]*:[\w\-]+)\s+([a-zA-Z_][\w\-]*:[\w\-]+)\s*[;.]'
    )

    for line in lines:
        stripped = line.strip()

        if not stripped:
            inside_named_individual_block = False
            current_subject = None
            new_lines.append(line)
            continue

        # A non-indented line starts a new RDF subject.
        if not line[0].isspace():
            m_subject = subject_pattern.match(line)

            if m_subject:
                current_subject = m_subject.group(1)
                transformed_classes.add(current_subject)
                inside_named_individual_block = True
                subclass_predicate_written = False

                line = re.sub(
                    r'\s+(?:a|rdf:type)\s+owl:NamedIndividual\s*[,;]?',
                    ' a owl:Class ;',
                    line,
                    count=1,
                )
            else:
                # This subject is not a NamedIndividual.
                current_subject = None
                inside_named_individual_block = False

            new_lines.append(line)
            continue

        # Only transform continuation lines belonging to a NamedIndividual.
        if inside_named_individual_block and current_subject:
            # Example:
            #     vin:Region .
            # becomes:
            #     rdfs:subClassOf vin:Region .



            bare_type_match = re.match(
                r'^(\s*)((?:[A-Za-z_][\w\-]*)?:[\w\-]+)(\s*)([,;.])\s*$',
                line,
            )

            if bare_type_match:
                indent = bare_type_match.group(1)
                parent_class = bare_type_match.group(2)
                spacing = bare_type_match.group(3)
                terminator = bare_type_match.group(4)

                if not subclass_predicate_written:
                    line = (
                        f"{indent}rdfs:subClassOf {parent_class}"
                        f"{spacing}{terminator}"
                    )
                    subclass_predicate_written = True
                else:
                    line = (
                        f"{indent}{parent_class}"
                        f"{spacing}{terminator}"
                    )

            # bare_type_match = re.match(
            #     r'^(\s*)((?:[A-Za-z_][\w\-]*)?:[\w\-]+)(\s*)([.;])\s*$',
            #     line,
            # )

            # if bare_type_match:
            #     indent = bare_type_match.group(1)
            #     parent_class = bare_type_match.group(2)
            #     spacing = bare_type_match.group(3)
            #     terminator = bare_type_match.group(4)

            #     line = (
            #         f"{indent}rdfs:subClassOf {parent_class}"
            #         f"{spacing}{terminator}"
            #     )

            # Example:
            #     a vin:Region ;
            # becomes:
            #     rdfs:subClassOf vin:Region ;
            line = re.sub(
                r'^(\s*)a\s+(?!owl:Class\b)([^\s;,.]+)(\s*[;.])',
                r'\1rdfs:subClassOf \2\3',
                line,
            )

            m_relation = relation_pattern.match(line)
            if m_relation:
                predicate = m_relation.group(1)
                target = m_relation.group(2)

                if predicate != "rdfs:subClassOf":
                    object_relations.append(
                        (predicate, current_subject, target)
                    )

        new_lines.append(line)

        if line.rstrip().endswith("."):
            inside_named_individual_block = False
            current_subject = None

   
    # Adding inferred ObjectProperty definitions
    property_blocks = []
    seen = set()

    for predicate, domain, range_ in object_relations:
        key = (predicate, domain, range_)

        if key in seen:
            continue

        seen.add(key)

        local_name = predicate.split(":", 1)[1] if ":" in predicate else predicate

        property_blocks.append(
            f"\n{predicate}\n"
            f"    a rdf:Property ;\n"
            f"    a owl:ObjectProperty ;\n"
            f"    rdfs:domain {domain} ;\n"
            f"    rdfs:range {range_} .\n"
            f"\n{predicate}\n"
            f'    rdfs:label "{local_name}" .\n'
        )

    output_ttl.write_text(
        "".join(new_lines) + "\n" + "".join(property_blocks),
        encoding="utf-8"
    )

    return transformed_classes


def run_ontogram(modified_ttl: Path) -> Path:
    """
    Run ontogram CLI on the modified TTL.

    Ontogram CLI generates:
      <file>.txt
      <file>.png
      <file>.svg
    """
    ontogram_exe = shutil.which("ontogram")
    if not ontogram_exe:
        raise RuntimeError(
            "Could not find 'ontogram' in PATH. Install it and make sure the CLI is available."
        )

    cmd = [ontogram_exe, str(modified_ttl)]
    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode != 0:
        raise RuntimeError(
            f"ontogram failed with exit code {result.returncode}\n"
            f"STDOUT:\n{result.stdout}\n\nSTDERR:\n{result.stderr}"
        )

    plantuml_txt = Path(str(modified_ttl) + ".txt")
    if not plantuml_txt.exists():
        raise FileNotFoundError(
            f"Ontogram finished but expected PlantUML file was not found: {plantuml_txt}"
        )

    return plantuml_txt


def extract_class_uri_from_line(line: str) -> str | None:
    """
    Extract URI from PlantUML lines like:
      Class ":CFIHOS-30000360T" [[http://example.com/CFIHOS-30000360T]] {
    """
    m = re.search(r"\[\[([^\]]+)\]\]", line)
    if m:
        return m.group(1).strip()
    return None


def add_marker_after_class_name(line: str, marker: str = MARKER) -> str:
    """
    Insert marker immediately after the class name.

    Example:
      Class ":A" [[http://example.com/A]] {
    becomes:
      Class ":A"<<(i,#FF7700)>> [[http://example.com/A]] {
    """
    if marker in line:
        return line

    pattern = re.compile(
        r'^(\s*(?:Class|class|entity|component|interface)\s+)'
        r'("([^"]+)"|[^\s\[\{]+)'
        r'(.*)$'
    )

    m = pattern.match(line.rstrip("\n"))
    if not m:
        return line

    prefix = m.group(1)
    class_name = m.group(2)
    suffix = m.group(4)

    return f"{prefix}{class_name}{marker}{suffix}\n"

def enrich_plantuml_by_namespace(
    input_txt: Path,
    output_txt: Path,
    namespace: str,
    marker: str = MARKER,
) -> None:
    """
    Enrich PlantUML lines generated by ontogram.
    Add marker to classes whose URI starts with the given namespace.
    """
    lines = input_txt.read_text(encoding="utf-8").splitlines(keepends=True)
    enriched_lines: list[str] = []

    class_decl_pattern = re.compile(r'^\s*(Class|class|entity|component|interface)\b')

    for line in lines:
        if class_decl_pattern.match(line):
            uri = extract_class_uri_from_line(line)
            if uri and uri.startswith(namespace):
                line = add_marker_after_class_name(line, marker)

        enriched_lines.append(line)

    output_txt.write_text("".join(enriched_lines), encoding="utf-8")


def enrich_plantuml_by_class_name(
    input_txt: Path,
    output_txt: Path,
    class_name_set: set,
    marker: str = MARKER,
) -> None:
    """
    Add the marker to the declaration of the specified PlantUML class.

    Parameters
    ----------
    input_txt : Path
        Input PlantUML file.
    output_txt : Path
        Output PlantUML file.
    class_name : str
        Name of the PlantUML class to be marked (e.g. ':Person').
    marker : str
        Marker inserted after the class name.
    """

    lines = input_txt.read_text(encoding="utf-8").splitlines(keepends=True)
    enriched_lines = []

    pattern = re.compile(
        r'^\s*(?:Class|class|entity|component|interface)\s+'
        r'("([^"]+)"|([^\s\[\{]+))'
    )

    for line in lines:
        m = pattern.match(line)
        if m:
            declared_name = m.group(2) or m.group(3)
            if declared_name in class_name_set:
                line = add_marker_after_class_name(line, marker)

        enriched_lines.append(line)

    output_txt.write_text("".join(enriched_lines), encoding="utf-8")



'''
rdflib sometimes generates prefixes such as alice:, mean:, ns1:, ns2:, etc., when serializing RDF because it is trying to create a readable prefix for a namespace that does not already have one bound.
'''

def get_qname_local_part(value: str) -> str | None:
    """
    Return the substring after ':' in a QName-like value.

    Examples:
      ex:75043    -> 75043
      meant:75043 -> 75043
      :75043      -> 75043
    """
    if not value or ":" not in value:
        return None

    _, local_part = value.split(":", 1)
    return local_part or None


def extract_plantuml_class_name_set(input_txt: Path) -> set[str]:
    """
    Extract declared PlantUML class names from lines like:
      Class "ex:75043" [[...]] {
      Class "meant:75043" <<...>> [[...]] {
    """
    lines = input_txt.read_text(encoding="utf-8").splitlines()
    class_names: set[str] = set()

    pattern = re.compile(
        r'^\s*(?:Class|class|entity|component|interface)\s+'
        r'("([^"]+)"|([^\s\[\{]+))'
    )

    for line in lines:
        m = pattern.match(line)
        if m:
            class_names.add(m.group(2) or m.group(3))

    return class_names


def identify_uri_prefix_changes(initial_set: set[str], final_set: set[str]) -> dict[str, str]:
    """
    Identify individuals whose local part is the same but whose prefix changed.

    Returns a dict where:
      key   = changed/final individual, e.g. "meant:75043"
      value = original/initial individual, e.g. "ex:75043"
    """
    initial_by_local_part: dict[str, str] = {}

    for initial_individual in initial_set:
        local_part = get_qname_local_part(initial_individual)
        if local_part:
            initial_by_local_part[local_part] = initial_individual

    changed_individuals: dict[str, str] = {}

    for final_individual in final_set:
        local_part = get_qname_local_part(final_individual)
        if not local_part:
            continue

        original_individual = initial_by_local_part.get(local_part)
        if original_individual and final_individual != original_individual:
            changed_individuals[final_individual] = original_individual

    return changed_individuals


def replace_changed_individuals_in_text_file(
    input_txt: Path,
    output_txt: Path,
    changed_individuals: dict[str, str],
) -> None:
    """
    Replace every changed individual occurrence in a text file using whole-token
    matching, so "meant:75043" is replaced by "ex:75043" but partial strings
    are not accidentally changed.
    """
    text = input_txt.read_text(encoding="utf-8")

    for changed_individual, original_individual in sorted(
        changed_individuals.items(),
        key=lambda item: len(item[0]),
        reverse=True,
    ):
        pattern = re.compile(
            rf'(?<![A-Za-z0-9_:\-]){re.escape(changed_individual)}(?![A-Za-z0-9_:\-])'
        )
        text = pattern.sub(original_individual, text)

    output_txt.write_text(text, encoding="utf-8")

def identifies_and_corrects_changes_in_uri(
    initial_set: set[str],
    final_set: set[str],
    text_file: Path | None = None,
    output_file: Path | None = None,
) -> dict[str, str]:
    """
    Identify whether individuals from initial_set were transformed in final_set
    by changing only their prefix, and optionally correct those occurrences in
    a text file.

    Example:
      initial_set = {"ex:75043"}
      final_set   = {"meant:75043"}

    The function detects:
      {"meant:75043": "ex:75043"}

    If text_file is provided, all occurrences of "meant:75043" are replaced by
    "ex:75043" in output_file. If output_file is not provided, text_file is
    modified in place.
    """
    changed_individuals = identify_uri_prefix_changes(initial_set, final_set)

    if text_file and changed_individuals:
        target_file = output_file or text_file
        replace_changed_individuals_in_text_file(
            Path(text_file),
            Path(target_file),
            changed_individuals,
        )

    return changed_individuals


''' 
Group of functions to add attributes in the classes
'''
def load_ontology(ttl_file: str)-> Graph:
    g = Graph()
    g.parse(ttl_file, format="turtle")
    return g

def extract_class_info(graph: Graph) -> dict:
    class_info = {}

    for s in graph.subjects(RDF.type, OWL.Class):
        class_name = str(s).split("/")[-1]

        properties = []

        # rdf:type
        # properties.append("a owl:Class")

        # rdfs:label
        for label in graph.objects(s, RDFS.label):
            properties.append(f'rdfs:label "{label}"')

        class_info[class_name] = properties

    return class_info

def enrich_plantuml_adding_attributes(ttl_file: str, plantuml_file: str, output_file: str) -> None:
   
    graph = load_ontology(ttl_file)
    class_info = extract_class_info(graph)

    with open(plantuml_file, "r", encoding="utf-8") as f:
        content = f.read()

    def replace_block(match):
        full_block = match.group(0)
        class_name = match.group(1)

        # Remove leading ":" if exists
        clean_name = class_name.replace(":", "")

        if clean_name in class_info:
            props = class_info[clean_name]

            props_text = "\n  " + "\n  ".join(props) + "\n"
            return re.sub(r"\{\s*\}", "{"+props_text+"}", full_block)

        return full_block

    # Match PlantUML class blocks
    pattern = r'Class\s+"(:?[^"]+)"\s+\[\[[^\]]+\]\]\s*\{\s*\}'

    new_content = re.sub(pattern, replace_block, content)
    with open(output_file, "w", encoding="utf-8", newline="") as f:
        f.write(new_content)
        f.flush()

    if not output_file.exists():
        raise RuntimeError(f"File was not created: {output_file}")

    print(f"Saved modified plantUML: {output_file}")



def build_output_paths(input_ttl: Path, outdir: Path) -> tuple[Path, Path]:
    """
    Returns:
      modified_ttl
      enriched_plantuml_txt
    """
    stem = input_ttl.stem
    modified_ttl = outdir / f"{stem}.modified.ttl"
    enriched_txt = outdir / f"{stem}.modified.ttl.enriched.txt"
    return modified_ttl, enriched_txt


def main() -> int:
    parser = argparse.ArgumentParser(
        # description="Convert owl:NamedIndividual to owl:Class, run ontogram, and enrich PlantUML output by namespace."
        description="Convert owl:NamedIndividual to owl:Class, run ontogram, and enrich PlantUML output."
    )
    parser.add_argument("input_ttl", help="Input Turtle (.ttl) file")
    parser.add_argument(
        "--namespace",
        required=False,
        help='Namespace URI to mark, e.g. "http://example.com/"',
    )
    parser.add_argument(
        "--outdir",
        default="output",
        help="Output directory (default: output)",
    )
    parser.add_argument(
        "--marker",
        default=MARKER,
        help='Marker/stereotype to inject (default: "<<(i,#FF7700)>>")',
    )

    args = parser.parse_args()

    input_ttl = Path(args.input_ttl).resolve()
    outdir = Path(args.outdir).resolve()
    namespace = args.namespace
    marker = args.marker

    if not input_ttl.exists():
        print(f"Input file not found: {input_ttl}", file=sys.stderr)
        return 1

    outdir.mkdir(parents=True, exist_ok=True)

    modified_ttl, enriched_txt = build_output_paths(input_ttl, outdir)

    try:
    
        print(f"[1/7]  Completing domain/range from inverse properties...")

        enrich_object_properties_with_inverse_domain_range(
            input_file=str(input_ttl),
            output_file=str(modified_ttl)
        )
        print(f"      Completed TTL written to: {modified_ttl}")

        print(f"[2/7]  Converting ttl style...")

        convert_ttl_style(
            input_file=str(modified_ttl),
            output_file=str(modified_ttl)
        )
        print(f"      Completed TTL written to: {modified_ttl}")

        print(f"[3/7] Reading and transforming individuals object in the TTL: {input_ttl}")
        set_of_named_individuals = transform_ttl_named_individual_blocks(modified_ttl, modified_ttl)
        print(f"      Modified TTL written to: {modified_ttl}")

        try:
            Graph().parse(modified_ttl, format="turtle")
            print("Modified generated TTL is valid.")
        except Exception as e:
            raise RuntimeError(
                f"Generated TTL is invalid before Ontogram:\n{e}"
            )

        print(f"[4/7] Running ontogram on modified TTL")
        generated_txt = run_ontogram(modified_ttl)
        print(f"      Ontogram PlantUML file: {generated_txt}")


        print(f"      Verifying URI prefix changes")
        
        final_set_of_individuals = extract_plantuml_class_name_set(generated_txt)
        changed_individuals = identifies_and_corrects_changes_in_uri(
            initial_set=set_of_named_individuals,
            final_set=final_set_of_individuals,
            text_file=generated_txt,
            output_file=generated_txt,
        )

        if changed_individuals:
            print(f"      Corrected URI prefix changes: {changed_individuals}")

        print(f"[5/7] -- Enriching PlantUML, adding prototype for individuals: {set_of_named_individuals}")

        enrich_plantuml_by_class_name(
            generated_txt,
            enriched_txt,
            set_of_named_individuals,
            marker=marker,
        )
        print(f"      Enriched PlantUML written to: {enriched_txt}")

        print(f"[6/7] -- Enriching PlantUML, adding class attributes: {set_of_named_individuals}")
        enrich_plantuml_adding_attributes(
            modified_ttl,
            enriched_txt,
            enriched_txt
        )
        print(f"      Enriched PlantUML, adding attributes to classes written to: {enriched_txt}")

        print(f"[7/7] -- Enriching PlantUML, changes colors by namespace")
        add_color_by_namespace(
            enriched_txt,
            enriched_txt
        )
        print(f"      Enriched PlantUML, changes colors by namespace: {enriched_txt}")



        print(f"[7/7] Done")
        print("\nOutputs:")
        print(f"  Modified TTL:      {modified_ttl}")
        print(f"  Enriched PlantUML: {enriched_txt}")

        return 0

    except Exception:
        traceback.print_exc()
        return 2


if __name__ == "__main__":
    raise SystemExit(main())