#!/usr/bin/env python3
import sys
from pathlib import Path

import click
import lxml.etree as ET


XSLT_PATH = str(Path(__file__).resolve().parent.parent / "app" / "TEI2Ontolex.xsl")


@click.command()
@click.argument('input')
@click.argument('output', required=False)
def convert(input: str, output: str = None):
    if output is None:
        output = Path(input).with_suffix('.ontolex.xml')

    xml = ET.parse(input)
    xslt = ET.XSLT(ET.parse(XSLT_PATH), access_control=ET.XSLTAccessControl.DENY_ALL)
    output_fd = sys.stdout.buffer if output == '-' else open(output, 'wb')
    output_fd.write(ET.tostring(xslt(xml)))
    print('Written to', output, file=sys.stderr)
    print(xslt.error_log, file=sys.stderr)


if __name__ == '__main__':
    convert()
