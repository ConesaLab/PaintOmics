"""What ``current/species.json`` holds, in what order, written once.

Two installers write the file -- DBManager.generateAvailableSpeciesFile after
a standard install, customSpeciesInstaller.regenerate_species_json after a
custom one -- and each had its own loop and its own order. DBManager iterated
a ``set`` of codes, so the file came out in hash order: different on every
run, and with 3,000 organisms installed a ``_prev`` backup that differed from
the file on every line. The custom installer sorted by code.

The organism picker (app/view/common/OrganismSearch.js) orders what it shows
itself -- model organisms first, the rest by display name -- and never trusts
the file's order, so the order here is for whoever reads the file directly:
an administrator, a script, the "Request an organism" dialog's installed
check. By display name, case-insensitively, code as the tie-break, which is
the picker's own order for everything after the model organisms.
"""
import json
import os
import shutil


def species_json_rows(codes, names):
    """The ``(code, name)`` rows of species.json for ``codes``, in file order.

    ``names`` maps code -> display name and must hold every code: the callers
    check first, each with its own error, because a code without a name is an
    install that half happened. Duplicate codes collapse to one row.
    """
    return sorted(((code, names[code]) for code in set(codes)),
                  key=lambda row: (row[1].casefold(), row[0]))


def write_species_json(target, rows):
    """Write ``rows`` as species.json at ``target``.

    The previous file is kept beside it as ``<target>_prev``, as both
    installers did: a bad install can be undone by hand. ``json.dumps`` per
    value, not string concatenation, because a quote or backslash in an
    admin-supplied custom species name would otherwise write an unparseable
    file and break the organism dropdown for every user.
    """
    if os.path.isfile(target):
        shutil.copy(target, target + "_prev")
    entries = ",\n".join('\t{"name": %s, "value": %s}' % (json.dumps(name), json.dumps(code))
                         for code, name in rows)
    with open(target, "w") as fh:
        fh.write('{"success": true, "species": [\n' + entries + '\n]}')
    return target
