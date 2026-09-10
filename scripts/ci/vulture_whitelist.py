# Vulture whitelist: names vulture flags at >=80% confidence that are kept on
# purpose. Every entry is a PARAMETER -- part of a signature some caller,
# protocol or mock contract fixes -- so "unused" is the point, not a defect.
# Evidence per row lives in reports/deadcode.md (verdict: keep).
#
# Used by the lint gate as:  vulture PaintomicsServer/src scripts/ci/vulture_whitelist.py
# A name listed here counts as used; delete a row only when the parameter
# itself is gone from the signature it belongs to.

retry  # DBManager.download_command CLI parameter: documented in the command's help; dropping it breaks operator scripts that pass --retry=1
nDays  # DBManager.findolder_command CLI parameter: scriptine maps the command line onto this signature
transcripts_db_id  # common_build_database processKEGG*​MappingData parameters: all mapping processors share one call shape
armNames  # stategrafull.parseArmTime parameter with a default: callers may override the arm names
excType  # DBmanager.__exit__: the context-manager protocol fixes this three-argument signature
excValue  # DBmanager.__exit__: same protocol signature
mapGeneIDs  # FeatureNamesToKeggIDsMapper.mapFeatureNamesToKeggIDs parameter: executed under coverage, callers pass it
projection  # test fakes of pymongo find/find_one: the mock must accept the real API's arguments
cursorOptions  # test fake of pymongo find: the mapper passes batch_size through **cursorOptions
bridgeName  # test _FakeDB parameter: fixtures construct variants by name
return_document  # test fake of pymongo find_one_and_update: real API argument
silent  # test fake servlet-helper parameter: matches the production signature it stands in for
fileObj  # test fake parameter: matches the production signature it stands in for
LOG_FILE  # every scripts/*/build_database.py: argv[4] is the install log DBManager hands to each species build; the assignment documents that positional contract. It counted as "used" only through a debug print in dosa's old custom parser, removed 2026-09-11
