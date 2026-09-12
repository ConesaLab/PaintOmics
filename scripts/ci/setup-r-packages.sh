#!/usr/bin/env bash
#
# Install the R packages the pipeline's scripts load (metagenes, hub analysis)
# into a private library that the workflow caches. MORE is not among them: it
# runs on the more-rs binary, which scripts/ci/build-more-rs.sh provides.
#
# Plain install.packages() from CRAN binaries, on purpose: r-lib's
# setup-r-dependencies installed the same packages in 20 s and then hung for
# eight minutes in its "check whether quarto is needed" step, which was the
# whole PR budget. Nothing here needs pak, pandoc or quarto.
#
# Usage: scripts/ci/setup-r-packages.sh
# Env:   PAINTOMICS_CI_HOME  (default ~/paintomics-ci); the library is
#        $PAINTOMICS_CI_HOME/Rlib and is exported as R_LIBS_USER for the
#        following steps.

set -euo pipefail

CI_HOME="${PAINTOMICS_CI_HOME:-$HOME/paintomics-ci}"
RLIB="$CI_HOME/Rlib"
PACKAGES="mclust cluster amap factoextra purrr"

mkdir -p "$RLIB"
export R_LIBS_USER="$RLIB"

Rscript --vanilla - "$RLIB" $PACKAGES <<'EOF'
args <- commandArgs(trailingOnly = TRUE)
lib <- args[1]
wanted <- args[-1]
.libPaths(c(lib, .libPaths()))
have <- rownames(installed.packages(lib.loc = lib))
missing <- setdiff(wanted, have)
if (length(missing)) {
    cat("==> installing", paste(missing, collapse = ", "), "\n")
    options(warn = 2, Ncpus = max(1L, parallel::detectCores() - 1L))
    install.packages(missing, lib = lib, repos = "https://cloud.r-project.org",
                     quiet = TRUE)
} else {
    cat("==> R packages already in", lib, "\n")
}
bad <- wanted[!vapply(wanted, requireNamespace, logical(1), lib.loc = lib, quietly = TRUE)]
if (length(bad)) stop("R packages that do not load: ", paste(bad, collapse = ", "))
cat("==> R", as.character(getRversion()), "with", paste(wanted, collapse = ", "), "\n")
EOF

if [ -n "${GITHUB_ENV:-}" ]; then
    echo "R_LIBS_USER=$RLIB" >> "$GITHUB_ENV"
fi
