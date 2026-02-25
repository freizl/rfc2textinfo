#!/usr/bin/env python3
"""Convert RFC/Internet-Draft XML files to Texinfo Info format.

Uses xml2rfc's TexinfoWriter for rendering, with this script handling
orchestration: fetching XML, invoking the writer, running makeinfo,
and generating the Emacs Info dir file.

Usage:
    rfc2texi.py                Fetch and convert all specs listed in specs.conf
    rfc2texi.py --sync         Same as above (explicit)
    rfc2texi.py <file.xml>     Convert specific XML file(s)

The specs.conf file lists specs to fetch, one per line:
    rfc 9126                                  # Fetch RFC by number
    draft draft-ietf-oauth-browser-based-apps-26  # Fetch IETF draft
    url https://example.com/spec.xml name     # Fetch from arbitrary URL

Downloaded XML files are cached in xml/ subdirectory.
Generated .texi and .info files are placed alongside the script.
A dir file is generated for Emacs Info directory integration.
"""

import copy
import re
import sys
import os
import glob
import subprocess
import urllib.request

import xml2rfc
from xml2rfc.writers.base import default_options


# ---------------------------------------------------------------------------
# Build pipeline
# ---------------------------------------------------------------------------

RFC_URL = "https://www.rfc-editor.org/rfc/rfc{number}.xml"
DRAFT_URL = "https://www.ietf.org/archive/id/{name}.xml"


def fetch_xml(url, output_path):
    """Download XML from a URL. Skips if file already exists."""
    if os.path.exists(output_path):
        print(f"  [cached] {os.path.basename(output_path)}")
        return output_path
    print(f"  Fetching {url} ...")
    urllib.request.urlretrieve(url, output_path)
    return output_path


def parse_specs_conf(conf_path):
    """Parse specs.conf and return list of (url, output_name) tuples."""
    specs = []
    with open(conf_path, 'r') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            # Strip inline comments
            if '#' in line:
                line = line[:line.index('#')].strip()
            parts = line.split()
            kind = parts[0]
            if kind == 'rfc' and len(parts) >= 2:
                num = parts[1]
                url = RFC_URL.format(number=num)
                specs.append((url, f"rfc{num}"))
            elif kind == 'draft' and len(parts) >= 2:
                name = parts[1]
                url = DRAFT_URL.format(name=name)
                specs.append((url, name))
            elif kind == 'url' and len(parts) >= 3:
                url = parts[1]
                name = parts[2]
                specs.append((url, name))
            else:
                print(f"  Warning: skipping unrecognized line: {line}")
    return specs


def convert_file(filepath, output_dir=None, local_info_files=None):
    """Convert a single RFC XML file to .texi and .info using xml2rfc.

    Returns (info_basename, doc_id, title) on success, None on failure."""
    print(f"  Parsing {os.path.basename(filepath)} ...")

    basename = os.path.splitext(os.path.basename(filepath))[0]
    if output_dir is None:
        output_dir = os.path.dirname(filepath)
    texi_path = os.path.join(output_dir, basename + '.texi')
    info_path = os.path.join(output_dir, basename + '.info')

    # Build options from xml2rfc defaults, overriding what we need
    options = copy.deepcopy(default_options)
    options.quiet = True
    options.verbose = False
    options.allow_local_file_access = True
    options.utf8 = True
    options.vocabulary = 'v2v3'
    options.no_dtd = True
    options.liberal = True
    options.local_info_files = local_info_files or set()

    try:
        parser = xml2rfc.XmlRfcParser(filepath, quiet=True, options=options)
        xmlrfc = parser.parse(remove_comments=False, quiet=True, add_xmlns=True)

        # Convert v2 to v3 if needed
        if not xmlrfc.tree.getroot().get('prepTime'):
            v2v3 = xml2rfc.V2v3XmlWriter(xmlrfc, options=options, date=options.date)
            xmlrfc.tree = v2v3.convert2to3()

            prep = xml2rfc.PrepToolWriter(
                xmlrfc, options=options, date=options.date,
                liberal=True, keep_pis=[xml2rfc.V3_PI_TARGET])
            xmlrfc.tree = prep.prep()

        if xmlrfc.tree is None:
            print(f"  FAILED: prep tool returned no tree for {basename}")
            return None

        # Extract doc_id and title from prepped tree
        root = xmlrfc.tree.getroot()
        title_elem = root.find('./front/title')
        title = ''
        if title_elem is not None:
            title = ''.join(title_elem.itertext()).strip()

        rfc_num = root.get('number', '')
        doc_name = root.get('docName', '')
        doc_id = ''
        if rfc_num:
            doc_id = f"RFC {rfc_num}"
        elif doc_name:
            doc_id = doc_name
        # Also check seriesInfo
        if not rfc_num:
            for si in root.xpath('./front/seriesInfo') if hasattr(root, 'xpath') else root.findall('./front/seriesInfo'):
                if si.get('name') == 'RFC':
                    rfc_num = si.get('value', '')
                    doc_id = f"RFC {rfc_num}"
                    break

        # If the output basename looks like "rfc{N}", use that as
        # the canonical doc_id.  This handles specs whose XML comes
        # from a draft but whose published form is an RFC — e.g.
        # RFC 6749 is fetched from draft-ietf-oauth-v2-31.xml because
        # rfc-editor.org doesn't host XML for that era.
        rfc_basename_match = re.match(r'^rfc(\d+)$', basename)
        if rfc_basename_match and not rfc_num:
            doc_id = f"RFC {rfc_basename_match.group(1)}"

        writer = xml2rfc.TexinfoWriter(xmlrfc, options=options, date=options.date)
        writer.write(texi_path)

    except Exception as e:
        print(f"  ERROR in xml2rfc processing: {e}")
        import traceback
        traceback.print_exc()
        return None

    # Run makeinfo to compile to .info
    result = subprocess.run(
        ['makeinfo', '--no-split', '--force', '-o', info_path, texi_path],
        capture_output=True, text=True
    )
    if result.stderr.strip():
        warnings = result.stderr.strip().split('\n')
        shown = warnings[:5]
        for line in shown:
            print(f"    {line}")
        if len(warnings) > 5:
            print(f"    ... and {len(warnings) - 5} more warnings")

    if os.path.exists(info_path):
        print(f"  -> {os.path.basename(info_path)}")
        return (os.path.basename(info_path), doc_id, title)
    else:
        print(f"  FAILED: {os.path.basename(info_path)}")
        return None


def generate_dir_file(directory, entries):
    """Generate an Info dir file for the converted documents."""
    dir_path = os.path.join(directory, 'dir')
    lines = []
    lines.append('This is the file .../info/dir, which contains the')
    lines.append('topmost node of the Info hierarchy, called (dir)Top.')
    lines.append('')
    lines.append('\x1f')
    lines.append('File: dir,\tNode: Top\tThis is the top of the INFO tree')
    lines.append('')
    lines.append('* Menu:')
    lines.append('')
    lines.append('RFC and Internet-Draft Specifications')

    for info_basename, doc_id, title in sorted(entries, key=lambda e: e[1]):
        name = info_basename.replace('.info', '')
        label = doc_id if doc_id else name
        lines.append(f'* {label}: ({name}).  {title}.')

    lines.append('')

    with open(dir_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))
    print(f"\nGenerated dir file: {dir_path}")


def main():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    xml_dir = os.path.join(script_dir, 'xml')

    if len(sys.argv) >= 2 and sys.argv[1] == '--help':
        print(__doc__)
        sys.exit(0)

    # Determine what to convert
    if len(sys.argv) >= 2 and sys.argv[1] != '--sync':
        # Direct file arguments
        files = [os.path.abspath(f) for f in sys.argv[1:]]
    else:
        # Default: read specs.conf, fetch and convert
        conf_path = os.path.join(script_dir, 'specs.conf')
        if not os.path.exists(conf_path):
            print(f"No specs.conf found at {conf_path}")
            print("Create one or pass XML files as arguments.")
            sys.exit(1)

        specs = parse_specs_conf(conf_path)
        if not specs:
            print("No specs found in specs.conf")
            sys.exit(1)

        os.makedirs(xml_dir, exist_ok=True)
        print(f"Found {len(specs)} specs in specs.conf\n")

        files = []
        for url, name in specs:
            output_path = os.path.join(xml_dir, f"{name}.xml")
            try:
                fetch_xml(url, output_path)
                files.append(output_path)
            except Exception as e:
                print(f"  FAILED to fetch {name}: {e}")

    if not files:
        print("No files to convert.")
        sys.exit(1)

    # Build set of local info file basenames for cross-referencing
    local_info_files = set()
    for f in files:
        local_info_files.add(os.path.splitext(os.path.basename(f))[0])
    # Also include any existing .info files in the output directory
    for info_file in glob.glob(os.path.join(script_dir, '*.info')):
        local_info_files.add(os.path.splitext(os.path.basename(info_file))[0])

    entries = []
    for f in files:
        try:
            result = convert_file(f, output_dir=script_dir,
                                  local_info_files=local_info_files)
            if result:
                entries.append(result)
        except Exception as e:
            print(f"  ERROR converting {os.path.basename(f)}: {e}")

    if entries:
        generate_dir_file(script_dir, entries)

    print(f"\nConverted {len(entries)}/{len(files)} files.")


if __name__ == '__main__':
    main()
