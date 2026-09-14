"""Organism names for AI prompts."""
import csv
import os

from src.conf.serverconf import KEGG_DATA_DIR


def get_organism_name(organism_code):
    """The full species name for a KEGG organism code, from organisms_all.list;
    the code itself when the list is missing or does not name it."""
    filepath = os.path.join(KEGG_DATA_DIR, "current", "common", "organisms_all.list")
    try:
        with open(filepath) as handle:
            for row in csv.reader(handle, delimiter='\t'):
                if len(row) >= 3 and row[1] == organism_code:
                    return row[2].split("(")[0].strip()
    except FileNotFoundError:
        pass
    return organism_code
