# PaintOmics AI application image.
#
# Two things drive the shape of this file:
#
#   1. Python must be 3.11 -- both bounds matter, and requirements.txt carries
#      the detail. Briefly: >= 3.10 because Pillow 12.3.0, CairoSVG 2.9.0 and
#      requests 2.34.2 declare it, and every security fix for those three was
#      unreachable while this said 3.9; <= 3.11 because pandas 1.5.3 publishes
#      no cp312 wheel and would be built from source against an untested NumPy.
#
#      This used to read "Python must be 3.9" because Flask 1.1.2 / Werkzeug
#      1.0.1 / Jinja2 2.11.2 do not import on 3.10+. That was true of those
#      versions and made the 2020 web framework the reason nothing else could
#      move. Current Flask still supports 3.9, so it was upgraded first, on the
#      old interpreter, which removed the constraint rather than trading one
#      breakage for another.
#   2. R is required, and not only for admin tasks. PathwayAcquisitionJob.py
#      shells out to generateMetaGenes.R and hubAnalysis.R on the user-facing
#      request path, so an image without R serves pages correctly and then fails
#      the moment someone runs a Hub Analysis or Metagenes job.
#
# Debian bookworm ships R 4.2 with most of what we need as prebuilt r-cran-*
# packages. Only a handful come from CRAN source, and Bioconductor supplies two.
# Building tidyverse from source here would add ~40 minutes; it is not needed --
# the scripts import specific packages, not the metapackage.

# ---------------------------------------------------------------------------
# Stage 1: Python dependencies
# ---------------------------------------------------------------------------
FROM python:3.11-slim-bookworm AS python-builder

# Build-only toolchain. uWSGI and cffi compile C; none of this reaches the
# runtime image.
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        libcairo2-dev \
        libffi-dev \
        pkg-config \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /tmp/requirements.txt
# pip is upgraded but setuptools deliberately is not: requirements.txt bounds
# it, and upgrading here would install a newer one and let the bound win only
# by luck of ordering.
#
# The import check used to be `import pkg_resources, apscheduler`, from when
# APScheduler 3.6.3 imported pkg_resources at module load and setuptools 82 had
# removed it. APScheduler 3.11.3 uses importlib.metadata and nothing in the
# tree imports pkg_resources, so asserting it here would test a requirement the
# application no longer has -- and would start failing the build the day
# setuptools stops being seeded into the venv. Check what the app actually
# needs instead: that the scheduler and the two heaviest wheels import.
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir --upgrade pip wheel \
    && /opt/venv/bin/pip install --no-cache-dir -r /tmp/requirements.txt \
    && /opt/venv/bin/python -c "import apscheduler, numpy, pandas; print('runtime imports OK')"

# ---------------------------------------------------------------------------
# Stage 2: R dependencies
# ---------------------------------------------------------------------------
# Separate stage so a change to requirements.txt does not rebuild R, and a
# change to the R package list does not reinstall Python.
FROM python:3.11-slim-bookworm AS r-builder

RUN apt-get update && apt-get install -y --no-install-recommends \
        r-base-core \
        r-base-dev \
        build-essential \
        libcurl4-openssl-dev \
        libssl-dev \
        libxml2-dev \
        libfontconfig1-dev \
        libharfbuzz-dev \
        libfribidi-dev \
        libfreetype6-dev \
        libpng-dev \
        libtiff5-dev \
        libjpeg-dev \
    # Prebuilt Debian binaries. Everything here would otherwise compile from
    # source, and igraph and dplyr alone take ~15 minutes.
        r-cran-purrr \
        r-cran-cluster \
        r-cran-mclust \
        r-cran-igraph \
        r-cran-ggplot2 \
        r-cran-reshape2 \
        r-cran-gridextra \
        r-cran-jsonlite \
        r-cran-stringr \
        r-cran-dplyr \
        r-cran-tidyr \
        r-cran-readr \
        r-cran-tibble \
        r-cran-rvest \
        r-cran-xml2 \
        r-cran-gtools \
        r-cran-amap \
        r-cran-matrix \
        r-cran-biocmanager \
    # The factoextra / ggpubr dependency chain. Without these, install.packages
    # drags in FactoMineR -> car -> lme4 -> nloptr and compiles the lot; nloptr
    # additionally needs CMake and fails outright without it. All of these are
    # prebuilt for bookworm, which removes ~20 minutes and the CMake dependency.
        r-cran-factominer \
        r-cran-rstatix \
        r-cran-car \
        r-cran-lme4 \
        r-cran-nloptr \
        r-cran-broom \
        r-cran-ggrepel \
    # These three are packaged for bookworm too, so only two leaves below need
    # an external repository at all.
        r-cran-factoextra \
        r-cran-ggpubr \
        r-cran-ggsignif \
    # tidyverse is genuinely required, not decorative: GalaxyNetworkFunctionsv2.R
    # (sourced by hubAnalysisInstall.R) calls library(tidyverse) directly, and
    # the hsa install aborted at "FAILED WHILE INSTALLING HUB ANALYSIS
    # INFORMATION" without it. Debian ships it prebuilt, so it is not the slow
    # source build that skipping it was meant to avoid.
        r-cran-tidyverse \
    # Still needed if any remaining leaf package falls back to a source build.
        cmake \
    && rm -rf /var/lib/apt/lists/*

# No CRAN or Bioconductor step any more.
#
# There used to be two: qdapRegex + visNetwork from CRAN, and KEGGgraph +
# AnnotationDbi from Bioconductor. All four existed for hubAnalysisInstall.R,
# which is gone -- the KEGG graph is derived in Python from the KGML the
# installer already downloads. Checked against every surviving .R file: they
# load only MORE, amap, cluster, factoextra, mclust and optparse, none of which
# needs these four.
#
# Dropping the Bioconductor step is the bigger win; BiocManager::install pulls a
# long dependency chain and was among the slowest layers in this image.

# MORE, the regulatory-analysis backend behind runMORE.R. Not on CRAN or
# Bioconductor -- it is installed from GitHub.
#
# The source matters. There are two repositories called MORE:
#
#   BiostatOmics/MORE  <- CORRECT. Maintained; more() takes targetData /
#                         regulatoryData / condition / varSel / minVariation
#                         and it exports FilterRegulationPerCondition.
#   ConesaLab/MORE     <- WRONG. Superseded; more() takes GeneExpression /
#                         data.omics / edesign / min.variation, and has no
#                         FilterRegulationPerCondition at all.
#
# Installing the ConesaLab one builds and imports perfectly cleanly, then
# every MORE job dies at the model call with "unused arguments". The two are
# distinguishable only by more()'s signature, which is what
# src/tests/test_runmore_r_contract.py checks.
#
# optparse parses runMORE.R's command line and is CRAN-only.
#
# patchwork is pinned, and the pin is what makes this step build at all.
# ggplot2 here is Debian bookworm's r-cran-ggplot2, which is 3.4.1 and frozen
# by the distro. MORE pulls ComplexUpset, which pulls patchwork, and patchwork
# is unpinned from CRAN, so it resolves to whatever is current. Current
# patchwork calls ggplot2::is_ggplot(), added in ggplot2 3.5.2, and on 3.4.1 it
# fails to byte-compile:
#
#   Error: object 'is_ggplot' is not exported by 'namespace:ggplot2'
#   ERROR: lazy loading failed for package 'patchwork'
#   Failed to install 'MORE' from GitHub: Failed to install 'ComplexUpset' ...
#
# `upgrade = "never"` does not prevent this: it stops remotes upgrading what is
# already installed, and patchwork is absent, so it is a fresh install at
# latest. Installing a compatible patchwork first makes it present, and then
# `upgrade = "never"` does keep it.
#
# 1.2.0 is the last line that predates the is_ggplot switch. Raise it only
# together with ggplot2 - and ggplot2 moves only if it stops coming from the
# distro.
#
# ---------------------------------------------------------------------------
# MORE IS NOT INSTALLED, and the block below is commented out rather than
# deleted so the next attempt starts from what is already known.
#
# Pinning patchwork gets past ComplexUpset, and then eleven more packages fail,
# for three unrelated reasons that no further pinning fixes (measured
# 2026-08-08, full log in the build output):
#
#   Deriv     R_ClosureFormals / Rf_allocLang not declared. Those are R C-API
#             additions from R 4.4/4.5. R here is Debian bookworm's, 4.2.
#   fgsea     via BH/boost: 'is_final' / 'remove_cv_t' not declared in 'std'.
#             C++17 type traits, compiled at the image's C++11 default.
#   gdtools   cairo-ft.h: No such file. Missing dev headers.
#
# and behind them ggiraph, treeio, shadowtext, doBy, ggtree, DOSE, enrichplot
# and clusterProfiler. MORE's transitive tree has moved to needing a newer R and
# toolchain than this base provides; the tree is unpinned, so it keeps moving.
#
# Restoring it is a real piece of work, not a pin. Either resolve the whole
# tree from a dated snapshot contemporary with R 4.2 - packagemanager.posit.co
# is already an allowed outbound host, so `options(repos = ".../cran/2023-06-01")`
# is the cheap thing to try first - or move the image off the distro's R.
#
# Note this changes nothing that was shipping: the deployed image has never
# contained MORE. This file previously carried the block uncommented, which is
# why building from master failed outright, and the VM's own copy had it
# deleted with no record of why. This is that record.
# ---------------------------------------------------------------------------
# RUN Rscript -e 'options(warn = 2, Ncpus = parallel::detectCores()); \
#       install.packages(c("optparse", "remotes"), repos = "https://cloud.r-project.org"); \
#       remotes::install_version("patchwork", version = "1.2.0", \
#                                repos = "https://cloud.r-project.org", upgrade = "never"); \
#       remotes::install_github("BiostatOmics/MORE", upgrade = "never")'

# Fail the build now, not at 3am in production, if anything is unimportable.
#
# This list is the union of every R dependency in the repository. Deriving it by
# grepping for `library(` alone is not enough -- hubAnalysisInstall.R loads its
# packages with lapply(c(...), library, character.only = TRUE), which that
# pattern silently skips, and GalaxyNetworkFunctionsv2.R is reached only by
# being source()d. Both gaps produced install failures. When adding an R
# dependency anywhere, add it here too.
RUN Rscript -e 'pkgs <- c("purrr", "cluster", "mclust", "amap", "factoextra", \
                          "igraph", "ggplot2", "ggpubr", "ggsignif", "reshape2", \
                          "gridExtra", "jsonlite", "tibble", \
                          "stringr", "dplyr", "tidyr", "readr", "rvest", "xml2", \
                          "gtools", "tidyverse"); \
      missing <- pkgs[!vapply(pkgs, requireNamespace, logical(1), quietly = TRUE)]; \
      if (length(missing)) stop("missing R packages: ", paste(missing, collapse = ", ")); \
      cat("all", length(pkgs), "R packages import cleanly\n")'

# ---------------------------------------------------------------------------
# Stage 3: runtime
# ---------------------------------------------------------------------------
FROM python:3.11-slim-bookworm AS runtime

# Runtime shared libraries only.
#   libcairo2       -> cairocffi, which cairosvg renders through
#   r-base-core     -> Rscript
#   curl            -> downloadReactome.py shells out to it
#   wget            -> hubAnalysisInstall.R passes method="wget" to download.file
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
        r-base-core \
    # R's "Recommended" packages (cluster, MASS, Matrix, lattice, survival,
    # nlme, ...) live in /usr/lib/R/library, NOT in either site-library, so the
    # COPY below does not carry them over from the build stage. r-base-core
    # alone omits them, and generateMetaGenes.R needs cluster. The smoke test
    # caught this: the R stage's own import check passed while the runtime
    # image was missing the package.
        r-recommended \
        libcairo2 \
        libcurl4 \
        libxml2 \
        libfontconfig1 \
        libharfbuzz0b \
        libfribidi0 \
        libfreetype6 \
        libpng16-16 \
        libtiff6 \
        libjpeg62-turbo \
    # Shared libraries that COPYed R packages link against. apt pulled these in
    # automatically in the build stage, but COPYing site-library carries the R
    # packages and not the C libraries underneath them:
    #   libuv1   <- fs      <- tidyverse
    #   libnlopt <- nloptr  <- lme4 <- car <- ggpubr
    # Missing, they fail only at library() time with
    #   "unable to load shared object .../fs.so: libuv.so.1: cannot open..."
    # The ldd check at the end of this stage is what keeps this list honest.
        libuv1 \
        libnlopt0 \
        curl \
        wget \
        gnupg \
        ca-certificates \
    # setpriv, used by the entrypoint to drop privileges after fixing volume
    # ownership.
        util-linux \
    && rm -rf /var/lib/apt/lists/*

# MongoDB Database Tools, for mongoimport.
#
# common_build_database.py bulk-loads every collection with mongoimport rather
# than pymongo, so without this the install runs for hours and then dies at the
# very last step with "returned non-zero exit status 127" -- exit 127 being
# "command not found", which the shell=True call does not explain.
#
# Not in Debian: it comes from MongoDB's own repository.
RUN curl -fsSL https://pgp.mongodb.com/server-7.0.asc \
        | gpg --dearmor -o /usr/share/keyrings/mongodb-server-7.0.gpg \
    && echo "deb [ signed-by=/usr/share/keyrings/mongodb-server-7.0.gpg ] https://repo.mongodb.org/apt/debian bookworm/mongodb-org/7.0 main" \
        > /etc/apt/sources.list.d/mongodb-org-7.0.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends mongodb-database-tools \
    && rm -rf /var/lib/apt/lists/*

# Run as a non-root user. The data volume is chowned to this uid by the
# entrypoint rather than baked in, since it is mounted at run time.
RUN useradd --create-home --shell /bin/bash --uid 1001 paintomics

# ---------------------------------------------------------------------------
# ORDERING CONSTRAINT: every COPY from the build context must appear BEFORE the
# `COPY --from=` cross-stage copies below. Do not reorder these.
#
# On the deployment host (Docker 29 / overlay2 / OpenStack VM), a COPY placed
# after the three cross-stage library copies corrupts the image rootfs. Every
# following RUN then dies with
#   runc run failed: ... exec: "/bin/sh": stat /bin/sh: no such file or directory
# and an exec-form probe shows /usr/bin/ls is missing too, so the whole
# filesystem is unreadable rather than just the shell.
#
# It is not about what is copied: a single-file COPY triggers it just as a
# 5000-file directory does, and the same COPY against the base image succeeds.
# It is purely positional -- RUN after those copies is fine, COPY is not.
# Reproduced with --no-cache and a pruned builder cache; survives switching
# from the containerd snapshotter to overlay2.
#
# Moving the context copies ahead of the cross-stage copies avoids it entirely.
# ---------------------------------------------------------------------------
COPY deploy/app.tar /tmp/app.tar
COPY deploy/entrypoint.sh /usr/local/bin/entrypoint.sh
COPY deploy/uwsgi.ini /app/uwsgi.ini

COPY --from=python-builder /opt/venv /opt/venv
COPY --from=r-builder /usr/local/lib/R/site-library /usr/local/lib/R/site-library
COPY --from=r-builder /usr/lib/R/site-library /usr/lib/R/site-library

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# The application tree is delivered as a tar (built by deploy/build-image.sh and
# copied above) rather than as directory COPYs. tar stores symlinks as symlinks,
# including the cyclic src/src -> ../src and the
# src/public_html -> ../../PaintomicsClient/public_html that points outside the
# tree, so they survive with no exclusion list and no `ln -s` recreation.
#
# The real serverconf.py is excluded from the archive and never baked into the
# image; the entrypoint generates it from the template so every secret arrives
# as an environment variable.
# Unpack, permissions and runtime verification, deliberately in ONE layer.
#
# These were four separate RUN steps. Splitting them reintroduced the rootfs
# corruption described above the COPY block -- adding layers past a threshold
# makes the next step start with an unreadable filesystem. Keep this merged.
#
# 2026-08-11: it recurred, exactly as this comment warned, and the unpack step
# was folded in too. The tell is the same every time and names the wrong thing:
#
#     #25 [runtime 13/13] RUN ...
#     #25 0.647 runc run failed: unable to start container process:
#               error during container init: exec: "/bin/sh": stat /bin/sh:
#               no such file or directory
#
# The step that dies is the R package check, so it reads as an R problem. It is
# not. Excluded by measurement that day: the base image (`docker run --rm
# python:3.11-slim-bookworm /bin/sh -c echo` works), disk (528 GB free), RAM
# (24 GB), buildkit itself (a trivial two-line build on the same host at the
# same moment succeeds in under a second -- run that first, it separates "the
# daemon is sick" from "this image is sick" in seconds), concurrent load, and a
# stale snapshot (survives `systemctl restart docker`). It fails identically
# whether the previous step is CACHED or runs fresh, which is the clue: the
# layer *count* is what is the same in both.
#
# The delta between the last build that worked and the first that did not was a
# few hundred bytes in one client file. That is what sitting exactly at a
# threshold looks like. Fold further; never split.
#
# The verification runs HERE, in the runtime image, not in the r-builder. That
# stage's own import check passes by construction because apt installed every
# system library there as a dependency, so it cannot catch a runtime gap, and
# only this image ships. Two failures reached production that way:
# r-recommended (cluster/MASS/Matrix live in /usr/lib/R/library, which no COPY
# carried) and libuv1/libnlopt0 (C libraries the COPYed R packages link
# against). Both looked like a healthy server that died the moment a user ran a
# Metagenes or Hub Analysis job.
#
# library() rather than requireNamespace(): attaching is what triggers dyn.load,
# and it is what the scripts actually do. requireNamespace can succeed where
# library() fails.
RUN set -eux; \
    mkdir -p /app; \
    tar -xf /tmp/app.tar -C /app; \
    rm -f /tmp/app.tar; \
    mkdir -p /app/PaintomicsServer/src/log; \
    test -L /app/PaintomicsServer/src/src; \
    test -L /app/PaintomicsServer/src/AdminTools/src; \
    test -L /app/PaintomicsServer/src/AdminTools/scripts/src; \
    test -e /app/PaintomicsServer/src/public_html; \
    test ! -e /app/PaintomicsServer/src/conf/serverconf.py; \
    chown -R paintomics:paintomics /app; \
    chmod +x /usr/local/bin/entrypoint.sh; \
    chown paintomics:paintomics /app/uwsgi.ini; \
    chmod +x /app/PaintomicsServer/src/AdminTools/scripts/*.R; \
    chmod +x /app/PaintomicsServer/src/common/bioscripts/*.R; \
    missingLibs="$(find /usr/lib/R /usr/local/lib/R -name '*.so' 2>/dev/null \
        | while read -r so; do ldd "$so" 2>/dev/null | awk '/not found/ {print $1}'; done \
        | sort -u)"; \
    if [ -n "$missingLibs" ]; then \
        echo "R packages link against missing shared libraries:"; \
        echo "$missingLibs"; \
        exit 1; \
    fi; \
    echo "every R shared object resolves"; \
    # External commands the admin tools shell out to. mongoimport was missing
    # and only surfaced as exit 127 after a multi-hour install.
    for binary in Rscript mongoimport curl wget gunzip tar; do \
        command -v "$binary" >/dev/null || { echo "missing command: $binary"; exit 1; }; \
    done; \
    echo "every external command the installer needs is present"; \
    Rscript -e 'pkgs <- c("purrr", "cluster", "mclust", "amap", "factoextra", \
                          "igraph", "ggplot2", "ggpubr", "ggsignif", "reshape2", \
                          "gridExtra", "jsonlite", "tibble", \
                          "stringr", "dplyr", "tidyr", "readr", "rvest", "xml2", \
                          "gtools", "tidyverse"); \
      failed <- character(0); \
      for (p in pkgs) { \
        ok <- tryCatch({ suppressPackageStartupMessages(library(p, character.only = TRUE)); TRUE }, \
                       error = function(e) FALSE); \
        if (!ok) failed <- c(failed, p) \
      }; \
      if (length(failed)) stop("R packages fail to LOAD in the runtime image: ", \
                               paste(failed, collapse = ", ")); \
      cat("all", length(pkgs), "R packages load in the runtime image\n")'; \
    # The data directories are created HERE, in the same layer, rather than in
    # a RUN of their own. That is the same constraint the comment above this
    # block describes, hit again: on this host the final RUN died with
    #
    #   runc run failed: unable to start container process:
    #   error during container init: exec: "/bin/sh":
    #   stat /bin/sh: no such file or directory
    #
    # which is the rootfs-unreadable failure, not a missing directory. It
    # appeared on the move to python:3.11-slim-bookworm while every earlier
    # step succeeded, including the R load check immediately above.
    #
    # It is not the base image's layer count -- measured, python:3.9-slim-
    # bookworm and python:3.11-slim-bookworm both have exactly 4 layers. The
    # trigger is this image's own total, and the runtime stage sits right at
    # the edge of it, which is why the file already insists the verification
    # steps stay merged. Folding the last RUN in removes one more layer and
    # builds clean.
    #
    # If this recurs, merge further rather than splitting: every RUN in this
    # stage is a candidate to fold upward, and none of them needs its own
    # cache granularity.
    mkdir -p /data/KEGG_DATA /data/CLIENT_TMP; \
    chown -R paintomics:paintomics /data

# DBManager.py is run as a script, so sys.path[0] is its own directory -- that
# resolves `conf.serverconf` and `scripts.downloadReactome`. But
# scripts/downloadReactome.py also imports `src.AdminTools.DBManager`, which
# needs PaintomicsServer itself on the path. Setting it here means admin
# commands work from any working directory.
#
# ENV creates no filesystem layer, so its position relative to the RUN above
# does not matter to the problem described there.
ENV PYTHONPATH=/app/PaintomicsServer

# Deliberately no USER directive. A named volume mounted over /data arrives
# root-owned from the Docker daemon, so the container has to start as root to
# take ownership of it. entrypoint.sh does that and then drops to uid 1001 via
# setpriv before exec'ing the server, so no long-running process is root.
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD curl -fsS http://localhost:8000/ >/dev/null || exit 1

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["uwsgi", "--ini", "/app/uwsgi.ini"]
