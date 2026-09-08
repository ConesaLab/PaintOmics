#!/usr/bin/env Rscript

#***************************************************************
# MORE backend for PaintOmics AI
# Performs regulatory analysis using regression models.
#***************************************************************

# MORE comes from GitHub, not CRAN or Bioconductor:
#
#   remotes::install_github("BiostatOmics/MORE")
#
# Install that one specifically. A second, superseded package is also called
# MORE (ConesaLab/MORE) and installs perfectly cleanly, but its more() takes
# GeneExpression / data.omics / edesign / min.variation instead of the
# targetData / regulatoryData / condition / minVariation used below, and it
# does not export FilterRegulationPerCondition. The mismatch is invisible
# until a job reaches the model call and dies with "unused arguments", so
# src/tests/test_runmore_r_contract.py checks the installed signature against
# this call site.
suppressPackageStartupMessages({
  library(optparse)
  library(MORE)
})

option_list <- list(
  make_option(c("-t", "--target_file"), type="character", help="Target omic data (Gene Expression)"),
  make_option(c("-c", "--condition_file"), type="character", help="Experimental design / Conditions file"),
  make_option(c("-o", "--omic_names"), type="character", help="Comma-separated names of regulatory omics"),
  make_option(c("-d", "--data_files"), type="character", help="Comma-separated paths to regulatory data files"),
  make_option(c("-a", "--assoc_files"), type="character", help="Comma-separated paths to association files (or 'NULL')"),
  make_option(c("--min_variation"), type="character", default="0", help="Comma-separated per-omic minVariation (numeric, or 'NA' for the auto threshold). One value per omic, in --omic_names order; a single value applies to all."),
  make_option(c("-m", "--method"), type="character", default="PLS1", help="Model method: PLS1 or MLR"),
  make_option(c("--alpha"), type="numeric", default=0.05, help="Significance threshold (alpha)"),
  make_option(c("--vip"), type="numeric", default=0.8, help="VIP threshold for PLS1"),
  make_option(c("--filter_r2"), type="numeric", default=0.0, help="R2 filter threshold"),
  make_option(c("--output_dir"), type="character", help="Directory to save results"),
  make_option(c("--date_seed"), type="character", default="results", help="Prefix/seed for filenames")
)

opt <- parse_args(OptionParser(option_list=option_list))

# --- Helper: Robust Matrix Loading ---
read_matrix <- function(path) {
  if (is.null(path) || path == "NULL" || !file.exists(path)) {
    if (!is.null(path) && path != "NULL") cat(paste("MORE ERROR: File does not exist at path:", path, "\n"))
    return(NULL)
  }
  
  # Parse under each separator and keep whichever yields the most data columns.
  #
  # The previous version only fell back to comma when the tab parse *threw*,
  # and read.table(sep="\t") does not throw on a comma-separated file: every
  # line is a single field, row.names=1 consumes it, and the result is a
  # perfectly valid data frame with ZERO columns. as.matrix() then returned a
  # 2x0 logical matrix, is.null() was FALSE, and the job proceeded with no
  # data at all. The same silent path swallowed duplicate feature IDs, which
  # make the tab parse fail with "duplicate 'row.names' are not allowed" and
  # hand the comma attempt the same empty result.
  #
  # So the separator is chosen by what it produces, not by whether the first
  # attempt happened to raise, and a parse with no data columns is rejected
  # rather than propagated.
  # comment.char="" because PaintOmics' OWN format writes the header as `#gene`.
  # read.table defaults to comment.char="#", which strips that line -- so the
  # first DATA row becomes the header and its VALUES become the sample names.
  # Measured on a 3-feature file headed `#gene\ts1\ts2\ts3\ts4`:
  #
  #   as shipped   nrow=2  samples "1.0,2.0,3.0,4.0"  features G2,G3 (G1 LOST)
  #   with this    nrow=3  samples "s1,s2,s3,s4"      features G1,G2,G3
  #   the Rust port (unchanged)    same as "with this"
  #
  # So the two engines disagreed on any file carrying the convention this
  # project documents, and R was the one that was wrong. It usually surfaces
  # later as "No common sample names across input files", which sends the user
  # to look at their sample naming when nothing is wrong with it; twelve of the
  # shipped example datasets write `#`-headed values files, and so does every
  # file a user exports from PaintOmics.
  best <- NULL
  problems <- character(0)
  for (sep in c("\t", ",")) {
    parsed <- tryCatch(
      read.table(path, header=TRUE, sep=sep, check.names=FALSE, quote="\"",
                 row.names=1, comment.char=""),
      error = function(e) {
        problems <<- c(problems, paste0(if (sep == "\t") "tab" else "comma",
                                        ": ", conditionMessage(e)))
        NULL
      })
    if (!is.null(parsed) && ncol(parsed) > 0 &&
        (is.null(best) || ncol(parsed) > ncol(best))) {
      best <- parsed
    }
  }

  if (is.null(best)) {
    cat(paste0("MORE ERROR: no data columns could be read from ", path,
               ". Tried tab and comma separators.",
               if (length(problems) > 0) paste0(" (", paste(problems, collapse="; "), ")")
               else " Check the separator, and that feature IDs in the first column are unique.",
               "\n"))
    return(NULL)
  }

  # A header with no rows under it parses cleanly and gives a 0-row matrix,
  # which is not null and so passes every caller's guard. There is no valid
  # input here for any of the three call sites: no features, no samples.
  if (nrow(best) == 0) {
    cat(paste0("MORE ERROR: ", path,
               " has a header but no data rows.\n"))
    return(NULL)
  }

  data <- as.matrix(best)

  # as.matrix coerces the WHOLE matrix to character if any single cell is
  # non-numeric -- a stray "N/A", a description column, a thousands separator.
  # MORE then dies somewhere inside the model fit with no reference to the
  # file that caused it, so name the offending columns here instead.
  if (!is.numeric(data) && !is.logical(data)) {
    bad <- names(best)[!vapply(best, function(col) is.numeric(col) || is.logical(col),
                               logical(1))]
    cat(paste0("MORE ERROR: ", path, " has non-numeric values in column(s): ",
               paste(head(bad, 10), collapse=", "),
               if (length(bad) > 10) paste0(" (+", length(bad) - 10, " more)") else "",
               ". Expected a numeric matrix with feature IDs in the first column.\n"))
    return(NULL)
  }

  return(data)
}

cat("MORE: Starting analysis...\n")
cat(paste("MORE: Target file path:", opt$target_file, "\n"))
cat(paste("MORE: Condition file path:", opt$condition_file, "\n"))

# 1. Load Primary Data
targetData <- read_matrix(opt$target_file)
condition  <- read_matrix(opt$condition_file)

if (is.null(targetData)) stop("Target file not found or invalid.")
if (is.null(condition)) stop("Condition file not found or invalid.")

cat(paste("MORE: Loaded target data with", nrow(targetData), "features.\n"))
cat(paste("MORE: First few target IDs:", paste(head(rownames(targetData), 5), collapse=", "), "\n"))

# 2. Load Regulatory Omics and Associations
omic_names  <- gsub(" ", "_", trimws(unlist(strsplit(opt$omic_names, ","))))
data_paths  <- unlist(strsplit(opt$data_files, ","))
assoc_paths <- unlist(strsplit(opt$assoc_files, ","))

regulatoryData <- list()
associations   <- list()

for (i in seq_along(omic_names)) {
  name <- omic_names[i]  # Already sanitised (spaces→underscores) when omic_names was parsed

  # Load Data
  reg_mat <- read_matrix(data_paths[i])
  if (is.null(reg_mat)) stop(paste("Data file for", name, "not found."))
  regulatoryData[[name]] <- reg_mat
  
  cat(paste("MORE: Loaded regulatory omic", name, "with", nrow(reg_mat), "features.\n"))
  cat(paste("MORE: First few regulator IDs:", paste(head(rownames(reg_mat), 5), collapse=", "), "\n"))
  
    # Load Association (Optional). MORE's documented contract:
    #   col 1 = target feature ID
    #   col 2 = regulator ID
    #   col 3 = interaction type / "area" (OPTIONAL — e.g. PROMOTER, 1st_EXON)
    # We auto-detect column order using only cols 1-2 so that a 3-col file
    # keeps its area column intact when a swap is needed. Files with >= 4
    # columns are rejected outright — silently truncating could hide a
    # malformed upload.
    a_path <- assoc_paths[i]
    if (a_path != "NULL") {
      # comment.char="" for the same reason as read_matrix above: `#` opens a
      # HEADER in this project's format, never a comment, and the default would
      # silently drop the first pair of any association file that uses it.
      assoc_df <- read.table(a_path, header=TRUE, sep="\t", stringsAsFactors=FALSE,
                             check.names=FALSE, comment.char="")
      cat(paste("MORE: Loaded association file for", name, "with", nrow(assoc_df), "rows and", ncol(assoc_df), "columns.\n"))
      cat(paste("MORE: First row of association file:", paste(assoc_df[1,], collapse=" | "), "\n"))

      n_cols <- ncol(assoc_df)
      if (n_cols < 2) {
        stop(paste0("Association file for omic '", name, "' has only ", n_cols,
                    " column(s). MORE requires 2 columns (target, regulator) ",
                    "with an optional 3rd column for interaction type."))
      }
      if (n_cols > 3) {
        stop(paste0("Association file for omic '", name, "' has ", n_cols,
                    " columns. MORE accepts at most 3 columns: target, regulator, ",
                    "and an optional interaction-type/area column. Please check the file."))
      }

      # Orientation detection uses ONLY the first two columns.
      col1_match_reg <- sum(assoc_df[[1]] %in% rownames(reg_mat))
      col2_match_reg <- sum(assoc_df[[2]] %in% rownames(reg_mat))

      cat(paste("MORE: Col 1 matches with reg IDs:", col1_match_reg, "\n"))
      cat(paste("MORE: Col 2 matches with reg IDs:", col2_match_reg, "\n"))

      # Neither column names a regulator that exists in the data file. There is
      # no orientation to detect and nothing MORE can model: every target ends
      # up with no regulators, the run "succeeds", and the user gets an empty
      # result with no reason given. Same diagnostic shape as the sample
      # alignment check below -- show both ID spaces so the mismatch is
      # visible without reopening the files.
      if (max(col1_match_reg, col2_match_reg) == 0) {
        stop(paste0(
          "Association file for omic '", name, "' shares no regulator IDs with its ",
          "data file, in either column.\n",
          "  association col 1: ", paste(head(unique(assoc_df[[1]]), 3), collapse=", "), "\n",
          "  association col 2: ", paste(head(unique(assoc_df[[2]]), 3), collapse=", "), "\n",
          "  ", name, " data file: ", paste(head(rownames(reg_mat), 3), collapse=", "), "\n",
          "Check that both files use the same regulator identifiers, and that the ",
          "association file has a header row (its first line is read as one)."))
      }

      if (col1_match_reg > col2_match_reg) {
        cat("MORE: Detected Regulator in Column 1. Swapping to [Target, Regulator(, Area)]...\n")
        # Swap cols 1 <-> 2 and keep col 3 in place if it exists.
        if (n_cols == 3) {
          assoc_df <- assoc_df[, c(2, 1, 3), drop=FALSE]
        } else {
          assoc_df <- assoc_df[, c(2, 1), drop=FALSE]
        }
      }

      # Rename only the columns we own; preserve col 3's user-provided name
      # if present, or default it to "area" (the column name MORE itself uses
      # internally — see MORE:::GetAllReg).
      new_names <- colnames(assoc_df)
      new_names[1] <- "target"
      new_names[2] <- "regulator"
      if (n_cols == 3 && (is.na(new_names[3]) || new_names[3] == "")) {
        new_names[3] <- "area"
      }
      colnames(assoc_df) <- new_names

      if (n_cols == 3) {
        cat(paste("MORE: Detected optional interaction-type column '",
                  colnames(assoc_df)[3], "' — will be propagated to RegulationPerCondition.\n", sep=""))
      }

      # Check target overlap too
      target_overlap <- sum(assoc_df$target %in% rownames(targetData))
      cat(paste("MORE: Number of unique targets in association file:", length(unique(assoc_df$target)), "\n"))
      cat(paste("MORE: Number of targets in association file that exist in expression data:", target_overlap, "\n"))

      # Regulators line up but targets do not: every association points at a
      # feature the expression matrix has never heard of, so again there is
      # nothing to model and the run would finish empty and unexplained.
      if (target_overlap == 0) {
        stop(paste0(
          "Association file for omic '", name, "' shares no target IDs with the ",
          "target expression file.\n",
          "  association targets: ", paste(head(unique(assoc_df$target), 3), collapse=", "), "\n",
          "  expression features: ", paste(head(rownames(targetData), 3), collapse=", "), "\n",
          "Both files must identify features the same way (same ID type, same case)."))
      }

      associations[[name]] <- assoc_df
    } else {
      cat(paste("MORE: No association file provided for", name, ". MORE will use all-to-all or internal mapping.\n"))
      associations[[name]] <- NULL
    }
}

# 3. Sample Alignment (CRITICAL)
# Strict name-based intersection. We deliberately do NOT fall back to positional
# alignment the way MORE's R API does: in a web context the user does not see
# console warnings, and silently aligning differently-named samples by column
# order is the canonical way to publish statistically meaningless results.
# Force the user to harmonise sample names upstream so the 1:1 mapping is
# explicit and verifiable.
common_samples <- intersect(colnames(targetData), rownames(condition))
for (mat in regulatoryData) {
  common_samples <- intersect(common_samples, colnames(mat))
}

cat(paste("MORE: Found", length(common_samples), "common samples among all datasets.\n"))
if (length(common_samples) == 0) {
  # Diagnostic error: surface the first few sample IDs from every input so the
  # user can spot the naming mismatch without re-opening their files.
  reg_lines <- sapply(names(regulatoryData), function(n) {
    paste0("  ", n, " samples: ",
           paste(head(colnames(regulatoryData[[n]]), 3), collapse=", "),
           if (ncol(regulatoryData[[n]]) > 3) ", ..." else "")
  })
  stop(paste0(
    "No common sample names across input files.\n",
    "  Target samples:  ",
    paste(head(colnames(targetData), 3), collapse=", "),
    if (ncol(targetData) > 3) ", ..." else "", "\n",
    "  Condition rows:  ",
    paste(head(rownames(condition), 3), collapse=", "),
    if (nrow(condition) > 3) ", ..." else "", "\n",
    paste(reg_lines, collapse="\n"), "\n",
    "PaintOmics AI requires the same biological sample to carry the SAME column ",
    "name in the target expression file, the condition file, and every ",
    "regulatory omic file. Rename columns in R (e.g. ",
    "colnames(data.omics$miRNA) <- canonical_names) before saving so the ",
    "alignment is explicit. Positional alignment is intentionally not used."
  ))
}

# Re-order everything
targetData <- targetData[, common_samples, drop=FALSE]
condition  <- condition[common_samples, , drop=FALSE]
for (name in names(regulatoryData)) {
  regulatoryData[[name]] <- regulatoryData[[name]][, common_samples, drop=FALSE]
}

# 4. Run MORE
cat(paste("MORE: Running model (method =", opt$method, ")...\n"))

# If all associations are NULL, pass NULL instead of a list of NULLs
if (all(sapply(associations, is.null))) {
  associations_to_pass <- NULL
} else {
  associations_to_pass <- associations
}

# Configure parameters based on method
if (opt$method == "MLR") {
  varSel_val <- "EN"
} else {
  # For PLS1, use Jackknife by default as it is fast and robust
  varSel_val <- "Jack"
}

# Per-omic minVariation: low-variation filter applied independently to each
# regulatory omic. The servlet sends one token per omic (in omic_names order);
# "NA"/blank coerces to R's NA, which MORE reads as "auto" (10% of the maximum
# observed variability across conditions). A single token is recycled to all
# omics; an unexpected count falls back to 0 (MORE's documented default) rather
# than risking a silent mis-alignment between thresholds and omics.
parse_min_variation <- function(raw, omics) {
  tokens <- trimws(unlist(strsplit(raw, ",")))
  vals   <- suppressWarnings(as.numeric(tokens))  # "NA"/non-numeric -> NA (auto)
  if (length(vals) == 1L && length(omics) > 1L) {
    vals <- rep(vals, length(omics))
  }
  if (length(vals) != length(omics)) {
    warning(sprintf(
      "MORE: minVariation has %d value(s) for %d omic(s); using 0 for all.",
      length(vals), length(omics)))
    vals <- rep(0, length(omics))
  }
  names(vals) <- omics  # MORE matches the vector to regulatoryData by name
  vals
}
minVariation_vec <- parse_min_variation(opt$min_variation, names(regulatoryData))
cat(paste0("MORE: minVariation per omic -> ",
           paste(names(minVariation_vec),
                 ifelse(is.na(minVariation_vec), "NA (auto)", minVariation_vec),
                 sep = "=", collapse = ", "), "\n"))

# parallel is deliberately left at its default (FALSE). MORE 1.0.1 can fan the
# per-target fits out over future/furrr workers, but measured on this workload
# it is a pessimisation, not a speed-up:
#
#     genes   parallel=FALSE   parallel=TRUE
#        50         15.1 s          41.9 s
#       200         58.0 s         193.2 s
#
# A single target fit takes ~0.29 s, far too little to amortise worker startup
# and the serialisation of the regulator matrices to every worker, so the
# overhead dominates and grows with the number of targets.
#
# Those numbers are from a 12-core Apple M4 Pro laptop and are the *optimistic*
# case. The deployment VM is 8 vCPU (every OpenStack flavor available to the
# project caps there) with slower per-core throughput, so absolute times are
# longer and parallel is worse, not better: the per-task overhead is unchanged
# while there are fewer workers to spread it across. PySiQ also runs jobs in
# the web server's own process, so those workers would contend with request
# handling for the same 8 vCPUs.
#
# Cost is linear in targets and near-flat in samples. Budget from a measured
# per-gene rate on the target hardware rather than from the figures above, and
# re-measure before enabling parallel.
result_more <- more(
  targetData     = targetData,
  regulatoryData = regulatoryData,
  associations   = associations_to_pass,
  condition      = condition,
  method         = opt$method,
  varSel         = varSel_val,
  minVariation   = minVariation_vec,
  alfa           = opt$alpha,
  vip            = opt$vip
)

# 5. Extract and Write Results
if (!dir.exists(opt$output_dir)) dir.create(opt$output_dir, recursive=TRUE)

# Compute coefficients
result_rpc <- RegulationPerCondition(result_more)

# Optional R2 Filter
if (opt$filter_r2 > 0) {
  result_rpc <- FilterRegulationPerCondition(result_more, result_rpc, filterR2 = opt$filter_r2)
}

rpc_df <- as.data.frame(result_rpc)

# --- Repair regulator IDs mangled by MORE ------------------------------------
#
# MORE 1.0.1's RegulationPerCondition ends with
#
#     prefix = paste0(names(output$arguments$omicType), "-", collapse = "|")
#     myresults$regulator = gsub(prefix, "", myresults$regulator)
#
# to remove the "<omic>-" prefix it uses internally. That gsub is unanchored and
# global, so it deletes the omic name followed by a hyphen ANYWHERE in the ID,
# however many times it occurs. For an omic named "TF" a regulator genuinely
# called "TF-1" comes back as "1"; for an omic named "miRNA", "miRNA-21"
# becomes "21".
#
# It is not cosmetic. The values file is written from the input data and keeps
# the true ID, while the pairs file is written from this table, so the two stop
# agreeing -- and Job.parseGeneBasedFiles looks a values row up in the pairs
# file by GENE:::REGULATOR. Every red star for that omic silently disappears,
# and pathway enrichment moves with it, because enrichment counts significance.
#
# Repair by inverting the mangling against the IDs we actually loaded: apply the
# same substitution to every known regulator and map the result back. Ambiguous
# cases -- two real regulators mangling to the same string, or a mangled value
# that is itself a real ID -- are left untouched and reported, since guessing
# there would be a different silent corruption.

# Strip a leading "<omic>-" from regulator IDs, but never from an ID that is
# itself a real regulator. MORE only prefixes names it had to disambiguate, so
# "TF-1" under an omic named "TF" is far more likely to be a genuine ID than a
# prefixed "1" -- and after restore_regulator_ids has put such an ID back, an
# unconditional strip here would immediately destroy it again.
strip_omic_prefix <- function(values, prefix, trueIds) {
  values <- as.character(values)
  if (length(values) == 0) return(values)
  drop <- !(values %in% trueIds) & startsWith(values, prefix)
  values[drop] <- substring(values[drop], nchar(prefix) + 1)
  values
}

restore_regulator_ids <- function(values, trueIds, omicNames) {
  if (length(values) == 0 || length(trueIds) == 0) return(values)
  pattern <- paste0(omicNames, "-", collapse = "|")
  mangled <- gsub(pattern, "", trueIds)

  # Only IDs the substitution actually changes can need repair, and a mangled
  # form that collides with a real ID is not safely invertible.
  changed <- mangled != trueIds
  usable <- changed & !(mangled %in% trueIds) & !(duplicated(mangled) |
                                                 duplicated(mangled, fromLast = TRUE))
  if (!any(usable)) return(values)

  lookup <- setNames(trueIds[usable], mangled[usable])
  hit <- !(values %in% trueIds) & (values %in% names(lookup))
  if (any(hit)) {
    values[hit] <- unname(lookup[values[hit]])
    cat(paste0("MORE: restored ", sum(hit), " regulator ID(s) that MORE's ",
               "prefix removal had truncated (e.g. '", names(lookup)[1], "' -> '",
               lookup[[1]], "').\n"))
  }
  ambiguous <- changed & !usable
  if (any(ambiguous)) {
    cat(paste0("MORE WARNING: ", sum(ambiguous), " regulator ID(s) start with an ",
               "omic name and a hyphen and cannot be unambiguously restored: ",
               paste(head(trueIds[ambiguous], 5), collapse = ", "),
               ". They may appear truncated in the results.\n"))
  }
  values
}

if (nrow(rpc_df) > 0 && "regulator" %in% colnames(rpc_df)) {
  rpc_df$regulator <- as.character(rpc_df$regulator)
  for (.omic in omic_names) {
    .rows <- rpc_df$omic == .omic
    if (any(.rows)) {
      rpc_df$regulator[.rows] <- restore_regulator_ids(
        rpc_df$regulator[.rows], rownames(regulatoryData[[.omic]]), omic_names)
    }
  }
}

# Attach per-target R2 to rpc_df so the Step-3 Regulator-Target Network view
# can apply a post-hoc R2 slider client-side without re-querying the server.
# In MORE, GlobalSummary$GoodnessOfFit is a flat matrix with rownames = targetF
# and a method-dependent R2 column: MLR uses "Rsquared", PLS uses "RsquaredY".
# The R2/R-squared/Adj.R2 candidates are kept as defensive fallbacks in case a
# future MORE version renames the column.
gof <- result_more$GlobalSummary$GoodnessOfFit
.r2_candidates <- c("Rsquared", "RsquaredY", "R2", "R2.adj", "R-squared", "Adj.R2")

if (is.null(gof) || (is.list(gof) && length(gof) == 0)) {
  warning("MORE: GoodnessOfFit is empty or NULL; rpc table will lack R2")
} else {
  gof_df <- as.data.frame(gof)
  gof_df$targetF <- rownames(gof_df)
  r2_col <- intersect(.r2_candidates, colnames(gof_df))[1]
  if (!is.na(r2_col)) {
    colnames(gof_df)[colnames(gof_df) == r2_col] <- "R2"
    rpc_df <- merge(rpc_df, gof_df[, c("targetF", "R2")],
                    by = "targetF", all.x = TRUE, sort = FALSE)
    cat(paste0("MORE: attached R2 from GoodnessOfFit ('", r2_col, "')\n"))
  } else {
    # Surface the actual column names so future drift is debuggable without
    # source-diving into the MORE package.
    warning(sprintf(
      "MORE: no recognised R2 column in GoodnessOfFit (saw: %s); rpc table will lack R2",
      paste(colnames(gof_df), collapse = ", ")
    ))
  }
}

# Persist the full RegulationPerCondition table so PaintOmics' Step 3 panel
# can render it. Written here, BEFORE the per-omic loop below strips the
# `<omic>-` prefix in the yellow-star file — we apply the same prefix strip
# to the saved copy so what the user sees matches the regulator IDs in the
# original data file. The loop below still operates on the untouched rpc_df.
rpc_out_file <- file.path(opt$output_dir, paste0("MORE_rpc_", opt$date_seed, ".tab"))

if (nrow(rpc_df) > 0) {
  rpc_display <- rpc_df
  rpc_display$regulator <- as.character(rpc_display$regulator)
  for (.omic in omic_names) {
    .rows <- rpc_display$omic == .omic
    if (any(.rows)) {
      rpc_display$regulator[.rows] <- strip_omic_prefix(
        rpc_display$regulator[.rows], paste0(.omic, "-"),
        rownames(regulatoryData[[.omic]]))
    }
  }
  write.table(rpc_display, rpc_out_file,
              sep = "\t", row.names = FALSE, quote = FALSE, na = "")
  cat(paste0("MORE: wrote RegulationPerCondition table (",
             nrow(rpc_display), " rows, ", ncol(rpc_display), " cols) to ",
             basename(rpc_out_file), "\n"))
} else {
  # No relevant regulations at all — still create the file so Python can rely
  # on its existence; an absent file means MORE wasn't run at all.
  file.create(rpc_out_file)
  cat("MORE: RegulationPerCondition produced zero rows; wrote empty file.\n")
}

for (name in omic_names) {
  # Significance-filtered pairs (used ONLY for the yellow-star file).
  # The values file and the associations file both need every input pair, not just these.
  omic_df <- rpc_df[rpc_df$omic == name, , drop=FALSE]

  prefix   <- paste0(name, "-")
  reg_data <- regulatoryData[[name]]
  assoc_df <- associations[[name]]   # already normalised to columns: target, regulator (or NULL)

  # Build the full pair set: every (target, regulator) from the input association file
  # whose regulator is present in the regulator expression matrix. This matches the
  # miRNA2Genes contract — values + associations are an unfiltered snapshot of the
  # input data; significance only drives the yellow-star overlay.
  if (!is.null(assoc_df) && nrow(assoc_df) > 0) {
    keep_rows <- assoc_df$regulator %in% rownames(reg_data)
    full_pairs <- unique(assoc_df[keep_rows, c("target", "regulator"), drop=FALSE])
  } else {
    # No association file → fall back to MORE's significant pairs (best we can do).
    full_pairs <- unique(data.frame(
      target    = as.character(omic_df$targetF),
      regulator = strip_omic_prefix(omic_df$regulator, prefix, rownames(reg_data)),
      stringsAsFactors = FALSE
    ))
  }

  # A. Associations file (full set, 2-column TARGET\tREGULATOR for parseAssociationsFile).
  rel_assoc_file <- file.path(opt$output_dir, paste0("MORE_relevant_assoc_", name, "_", opt$date_seed, ".tab"))
  if (nrow(full_pairs) > 0) {
    write.table(full_pairs, rel_assoc_file, sep="\t", row.names=FALSE, col.names=FALSE, quote=FALSE)
  } else {
    file.create(rel_assoc_file)
  }

  # B. Relevant Pairs File — yellow-star source. Always significance-filtered (omic_df).
  # NOTE: We deliberately do NOT write MORE_relevant_reg_*.tab here. That file is the
  # red-star source and follows the miRNA2Genes contract: it only has content when the
  # user uploads a "Significant regulators" file. The MOREServlet creates it (empty or
  # expanded) after this script returns.
  rel_pairs_file <- file.path(opt$output_dir, paste0("MORE_relevant_pairs_", name, "_", opt$date_seed, ".tab"))
  if (nrow(omic_df) > 0) {
    pair_ids <- unique(paste0(
      as.character(omic_df$targetF),
      ":::",
      strip_omic_prefix(omic_df$regulator, prefix, rownames(reg_data))
    ))
    write.table(pair_ids, rel_pairs_file, sep="\t", row.names=FALSE, col.names=FALSE, quote=FALSE)
  } else {
    file.create(rel_pairs_file)
  }

  # C. Values file — one row per full pair, with the regulator's expression values.
  # Same set as the associations file so PA Step 1 sees every input pair (independent
  # of MORE's significance verdict). Header matches miRNA2Genes' "# Gene name\t..." style.
  val_file <- file.path(opt$output_dir, paste0("MORE_output_", name, "_", opt$date_seed, ".tab"))
  header <- paste(colnames(reg_data), collapse="\t")
  out_lines <- character(0)

  if (nrow(full_pairs) > 0) {
    for (j in seq_len(nrow(full_pairs))) {
      g <- as.character(full_pairs[j, "target"])
      r <- as.character(full_pairs[j, "regulator"])
      # Coerce R's NA → "NaN" so PA Step 1's validator (which calls float() on
      # every value) accepts the row. float("NA") raises; float("NaN") returns
      # nan. Real biological signal — e.g. CpG sites not measured in a sample
      # — would otherwise be silently rejected as "invalid values".
      v <- reg_data[r, ]
      v_char <- ifelse(is.na(v), "NaN", as.character(v))
      vals <- paste(v_char, collapse="\t")
      out_lines <- c(out_lines, paste0(g, ":::", r, "\t", vals))
    }
  }

  writeLines(c(paste0("# Gene name\t", header), out_lines), val_file)

  cat(paste("MORE:", name, "— wrote", length(out_lines), "pairs to values file (",
            nrow(omic_df), "significant for yellow stars)\n"))
}

cat("MORE: Analysis complete.\n")
