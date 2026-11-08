#!/usr/bin/env Rscript
#
# Extract the rows for one species from the Reactome "*2Reactome_PE_All_Levels"
# tables and write them to <root>/../<specie>/mapping/reactome/.
#
# Called by common_build_database.py::processReactomePathwaysData().
#
# Usage: processReactomeData.R --specie=hsa --root=/path/to/KEGG_DATA/current/common/

args <- commandArgs(TRUE)
# Each argument is cut at its FIRST "=" only, so a --root whose path holds "="
# (/data/KEGG=2026/current/common/) is read whole instead of being cut at it.
# Argument values are kept as given: the caller's shell has already removed its
# quotes. The same function as generateMetaGenes.R's; keep the two identical
# (test_r_arguments_keep_equals checks it). Base R only; checked under R 4.1.2.
parseArgs <- function(x) {
  x <- sub("^--", "", x[grepl("=", x, fixed = TRUE)])
  at <- regexpr("=", x, fixed = TRUE)
  values <- substring(x, at + 1L)
  names(values) <- substr(x, 1L, at - 1L)
  as.list(values)
}

args <- as.data.frame(parseArgs(args), stringsAsFactors = FALSE)

specie <- args$specie
ROOT <- args$root

if (is.null(specie) || is.na(specie) || specie == "") {
  stop("processReactomeData.R: --specie is required (e.g. --specie=hsa)")
}
if (is.null(ROOT) || is.na(ROOT) || ROOT == "") {
  stop("processReactomeData.R: --root is required")
}

ROOT_REACTOME <- paste0(ROOT, "../", specie, "/mapping/reactome/")

if (!dir.exists(ROOT_REACTOME)) {
  dir.create(ROOT_REACTOME, recursive = TRUE)
}

# ---------------------------------------------------------------------------
# KEGG organism code -> Reactome species name.
#
# The species column holds a full binomial ("Homo sapiens"). The previous
# implementation derived a KEGG code from it as first-letter + first-two-letters
# of the epithet. That silently produces the wrong code wherever KEGG does not
# follow the pattern -- rice is "osa" by the heuristic but "dosa" in KEGG and in
# this repo's resource directories -- and a wrong code means zero rows matched,
# no output file, and a build that still reported success.
#
# Explicit table first, heuristic only as a fallback for species not listed.
# ---------------------------------------------------------------------------
KEGG_TO_REACTOME_SPECIES <- c(
  hsa  = "Homo sapiens",
  mmu  = "Mus musculus",
  rno  = "Rattus norvegicus",
  ptr  = "Pan troglodytes",
  bta  = "Bos taurus",
  cfa  = "Canis familiaris",
  ssc  = "Sus scrofa",
  gga  = "Gallus gallus",
  xtr  = "Xenopus tropicalis",
  dre  = "Danio rerio",
  dme  = "Drosophila melanogaster",
  cel  = "Caenorhabditis elegans",
  sce  = "Saccharomyces cerevisiae",
  spo  = "Schizosaccharomyces pombe",
  ddi  = "Dictyostelium discoideum",
  pfa  = "Plasmodium falciparum",
  mtu  = "Mycobacterium tuberculosis",
  ath  = "Arabidopsis thaliana",
  dosa = "Oryza sativa",
  sly  = "Solanum lycopersicum"
)

speciesName <- unname(KEGG_TO_REACTOME_SPECIES[specie])

if (is.na(speciesName)) {
  cat(paste0("WARNING: '", specie, "' is not in the KEGG->Reactome species table; ",
             "falling back to name matching.\n"))
  speciesName <- NA
}

#' Read one mapping table, keep the rows for this species, and write them out.
#'
#' quote = "" and comment.char = "" are required. Reactome display names contain
#' apostrophes ("5'-phosphate") and double quotes, which R's defaults treat as
#' string delimiters -- silently merging rows and shifting every column.
processFile <- function(fileName, outputName, specie, speciesName) {
  inputPath <- paste0(ROOT, fileName, "_PE_All_Levels.txt")
  cat(paste0("Processing ", basename(inputPath), "...\n"))

  if (!file.exists(inputPath)) {
    stop(paste0("Reactome source file missing: ", inputPath,
                "\nRun the download step with --reactome=1 first."))
  }

  # Pre-filter with grep so only this species' rows are parsed. These tables run
  # to hundreds of MB across all species; read.delim on the whole file needs
  # several GB of RAM and minutes of parsing to then discard almost all of it.
  # The grep is a superset filter -- the exact match below still decides.
  filtered <- NULL
  if (!is.na(speciesName)) {
    filtered <- tryCatch(
      read.delim(pipe(paste("grep -F", shQuote(speciesName), shQuote(inputPath))),
                 header = FALSE, quote = "", comment.char = "",
                 stringsAsFactors = FALSE),
      error = function(e) NULL
    )
  }

  # Before paying for the fallback, ask grep -q whether the species truly has
  # zero rows here. A legitimately empty source (pfa/ddi/spo have no Ensembl
  # rows) used to route through the whole-file read.delim below -- a
  # several-GB, minutes-long parse of the largest tables just to confirm
  # nothing matched, on what is now a designed success path. Exit status 1
  # means "no match" with certainty; 0 (a match exists, so the pipe read above
  # is what failed) and 2 (grep errored) both fall through to the full read.
  zeroConfirmed <- FALSE
  if ((is.null(filtered) || nrow(filtered) == 0) && !is.na(speciesName)) {
    probeStatus <- suppressWarnings(
      system2("grep", c("-qF", shQuote(speciesName), shQuote(inputPath))))
    if (probeStatus == 1) {
      zeroConfirmed <- TRUE
      filtered <- data.frame(matrix(character(0), nrow = 0, ncol = 8),
                             stringsAsFactors = FALSE)
      colnames(filtered) <- paste0("V", 1:8)
    }
  }

  # Fallback: no species table entry, or grep produced nothing usable.
  if (!zeroConfirmed && (is.null(filtered) || nrow(filtered) == 0)) {
    inputData <- read.delim(inputPath, header = FALSE, quote = "",
                            comment.char = "", stringsAsFactors = FALSE)
    if (ncol(inputData) < 8) {
      stop(paste0("Unexpected column count (", ncol(inputData), ") in ", inputPath))
    }
    if (is.na(speciesName)) {
      # Heuristic: "Mus musculus" -> "mmu".
      codes <- tolower(paste0(
        substr(sub(" .*", "", inputData$V8), 1, 1),
        substr(sub("^\\S+ ", "", inputData$V8), 1, 2)))
      filtered <- inputData[codes == specie, , drop = FALSE]
    } else {
      filtered <- inputData[inputData$V8 == speciesName, , drop = FALSE]
    }
    rm(inputData)
    gc(verbose = FALSE)
  } else {
    if (ncol(filtered) < 8) {
      stop(paste0("Unexpected column count (", ncol(filtered), ") in ", inputPath))
    }
    filtered <- filtered[filtered$V8 == speciesName, , drop = FALSE]
  }

  if (nrow(filtered) == 0) {
    # Not fatal on its own. Reactome does not key every organism through every
    # identifier system: P. falciparum has 19k NCBI and 20k UniProt rows and
    # ZERO Ensembl rows (its genes live in PlasmoDB, not Ensembl), and the old
    # hard stop here failed the whole pfa build on the first file while the
    # other two carried the actual mappings. Write the empty output - the
    # Python side reads each mapping file independently and an empty one just
    # contributes no identifiers - and let the caller decide below whether
    # EVERY mapping came up empty, which is the real "not covered" case.
    cat(paste0("  WARNING: no rows matched species '", specie, "'",
               if (!is.na(speciesName)) paste0(" ('", speciesName, "')") else "",
               " in ", basename(inputPath), "; writing an empty mapping.\n"))
  }

  # Column 3 is "<id> <description>"; downstream code wants only the identifier.
  filtered$V3 <- sub(" .*", "", filtered$V3)

  outputPath <- paste0(ROOT_REACTOME, outputName, ".txt")
  write.table(filtered[, 1:4], file = outputPath, row.names = FALSE,
              col.names = FALSE, quote = FALSE, sep = "\t")
  cat(paste0("  Wrote ", nrow(filtered), " rows for ", specie, " -> ", outputPath, "\n"))

  rows <- nrow(filtered)
  rm(filtered)
  invisible(gc(verbose = FALSE))
  rows
}

cat("STEP 1: Processing Ensembl2Reactome...\n")
ensemblRows <- processFile("Ensembl2Reactome", "Ensembl2Reactome", specie, speciesName)

cat("STEP 2: Processing NCBI2Reactome...\n")
ncbiRows <- processFile("NCBI2Reactome", "NCBI2Reactome", specie, speciesName)

cat("STEP 3: Processing UniProt2Reactome...\n")
uniprotRows <- processFile("UniProt2Reactome", "UniProt2Reactome", specie, speciesName)

# Only every mapping being empty means Reactome truly does not cover the
# organism; that still has to fail loudly rather than install a Reactome
# database with no gene attached to anything.
if (ensemblRows + ncbiRows + uniprotRows == 0) {
  stop(paste0("No rows matched species '", specie, "' in any of ",
              "Ensembl2Reactome, NCBI2Reactome or UniProt2Reactome.\n",
              "Reactome does not cover every KEGG organism. Install this ",
              "species with --reactome=0, or add it to KEGG_TO_REACTOME_SPECIES."))
}

cat("All Reactome data processing completed successfully.\n")
