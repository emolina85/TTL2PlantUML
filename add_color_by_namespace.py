import re
import argparse


ELEMENT_PATTERN = re.compile(
    r'^(\s*)(Class|class|entity|component|interface)\b(.*)$'
)


def extract_element_name(rest: str) -> str | None:
    """
    Extrai o nome principal do elemento PlantUML.

    Suporta casos como:
      Class ":CFIHOS-30000360T" [[http://...]] {
      class my.namespace.MyClass {
      entity "Meu Elemento" as X
    """
    rest = rest.strip()

    # Caso com nome entre aspas
    quoted_match = re.match(r'^"([^"]+)"', rest)
    if quoted_match:
        return quoted_match.group(1)

    # Caso com identificador simples
    simple_match = re.match(r'^([^\s\{]+)', rest)
    if simple_match:
        return simple_match.group(1)

    return None


def add_stereotype_to_line(line: str, stereotype: str) -> str:
    """
    Adds <<stereotype>> immediately after the element name, without duplicating it.

    Example:
    Class ":CFIHOS-30000360T" [[http://...]]
    ->
    Class ":CFIHOS-30000360T"<<marked>> [[http://...]]
    """
    if f'<<{stereotype}>>' in line:
        return line

    line_no_nl = line.rstrip('\n')

    pattern = re.compile(
        r'^(\s*(?:Class|class|entity|component|interface)\s+)'
        r'("([^"]+)"|[^\s\[\{]+)'
        r'(.*)$'
    )

    match = pattern.match(line_no_nl)
    if not match:
        return line

    prefix = match.group(1)      # e.g. 'Class '
    element_name = match.group(2)  # e.g. '":CFIHOS-30000360T"'
    suffix = match.group(4)      # e.g. ' [[http://...]] {'

    return f'{prefix}{element_name}<<{stereotype}>>{suffix}\n'

def has_marked_skinparam(lines: list[str], stereotype: str) -> bool:
    pattern = re.compile(
        rf'BackgroundColor<<{re.escape(stereotype)}>>\s+\S+'
    )
    return any(pattern.search(line) for line in lines)


def insert_skinparam_block(lines: list[str], stereotype: str, color: str) -> list[str]:
    """
    Insere o bloco skinparam class antes de @enduml, se não existir.
    """
    if has_marked_skinparam(lines, stereotype):
        return lines

    block = [
        'skinparam class {\n',
        f'  BackgroundColor<<{stereotype}>> {color}\n',
        '}\n'
    ]

    for i, line in enumerate(lines):
        if line.strip().lower() == '@enduml':
            return lines[:i] + block + lines[i:]

    return lines + ['\n'] + block


def apply_mark_to_namespace(input_file: str, output_file: str, namespace: str, color: str, stereotype: str):
    with open(input_file, 'r', encoding='utf-8') as f:
        lines = f.readlines()

    new_lines = []

    for line in lines:
        match = ELEMENT_PATTERN.match(line)

        if match:
            _indent, _element_type, rest = match.groups()
            print(_indent, _element_type, rest)
            element_name = extract_element_name(rest)
            print("element_name", element_name)

            if element_name and  namespace in rest:
                line = add_stereotype_to_line(line, stereotype)

        new_lines.append(line)

    new_lines = insert_skinparam_block(new_lines, stereotype, color)

    with open(output_file, 'w', encoding='utf-8') as f:
        f.writelines(new_lines)

    print(f"Arquivo atualizado salvo em: {output_file}")


def test():
    apply_mark_to_namespace("arquivos_testes\\teste1_Geiza\Teste_ttl_enriched.ttl.txt", "arquivos_testes\\teste1_Geiza\Teste_ttl_enriched_coloured.ttl.txt", "http://data.cfihos.org/rdl",
                            "LightBlue", "marked",)
def main():
    parser = argparse.ArgumentParser(
        description="Marca elementos PlantUML por namespace usando stereotype"
    )
    parser.add_argument("--input", help="Arquivo PlantUML de entrada", default="arquivos_testes\\teste1_Geiza\Teste_ttl_enriched.ttl.txt")
    parser.add_argument("--output", help="Arquivo PlantUML de saída", default="arquivos_testes\\teste1_Geiza\Teste_ttl_enriched_coloured.ttl.txt")
    parser.add_argument("--namespace", help="Namespace/prefixo a procurar (ex.: :CFIHOS- ou my.ns)", default="http://data.cfihos.org/rdl")
    parser.add_argument("--color", default="LightBlue", help="Cor do stereotype marked")
    parser.add_argument("--stereotype", default="marked", help="Nome do stereotype a aplicar")

    args = parser.parse_args()

    apply_mark_to_namespace(
        input_file=args.input,
        output_file=args.output,
        namespace=args.namespace,
        color=args.color,
        stereotype=args.stereotype
    )


if __name__ == "__main__":
    main()
