"""Generate deterministic demo data; run from the repository root."""

import argparse

from kagent.data import make_data

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--path", default="data/company.db")
    make_data(parser.parse_args().path)
