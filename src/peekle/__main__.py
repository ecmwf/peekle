# (C) Copyright 2026- ECMWF and individual contributors.
#
# This software is licensed under the terms of the Apache Licence Version 2.0
# which can be obtained at http://www.apache.org/licenses/LICENSE-2.0.
# In applying this licence, ECMWF does not waive the privileges and immunities
# granted to it by virtue of its status as an intergovernmental organisation
# nor does it submit to any jurisdiction.
#

"""Command line interface for peekle."""

import argparse
import json
import sys

from .peekle import Peekle

# Keyword arguments understood by ``PeekleObject.to_json()``, exposed as
# ``--flag`` switches.
JSON_OPTIONS = {
    "shorten-strings": "Truncate strings longer than 20 characters",
    "shorten-bytes": "Only show the first 10 bytes of bytes values",
    "bytes-count": "Only show the length of bytes values",
    "function-calls": "Render callables and types as compact call strings",
}


def make_parser():
    parser = argparse.ArgumentParser(
        prog="peekle",
        description="Peek into pickle files without importing unknown dependencies.",
    )

    output = parser.add_mutually_exclusive_group()
    output.add_argument(
        "--json",
        action="store_true",
        help="Output the pickle contents as JSON (default)",
    )
    output.add_argument(
        "--python",
        action="store_true",
        help="Output the pickle contents as Python code",
    )

    for name, help in JSON_OPTIONS.items():
        parser.add_argument(
            f"--{name}",
            action="store_true",
            help=f"{help} (--json only)",
        )

    parser.add_argument(
        "--indent",
        type=int,
        default=2,
        help="Indentation level for JSON output (--json only, default: %(default)s)",
    )

    parser.add_argument("path", metavar="PATH", help="Path to the pickle file")

    return parser


def main(argv=None):
    parser = make_parser()
    args = parser.parse_args(argv)

    kwargs = {
        name.replace("-", "_"): getattr(args, name.replace("-", "_"))
        for name in JSON_OPTIONS
    }

    if args.python and any(kwargs.values()):
        parser.error("formatting options are only supported with --json")

    with open(args.path, "rb") as f:
        result = Peekle.parse(f)

    if args.python:
        print(result.to_python())
    else:
        print(json.dumps(result.to_json(**kwargs), indent=args.indent, default=str))

    return 0


if __name__ == "__main__":
    sys.exit(main())
