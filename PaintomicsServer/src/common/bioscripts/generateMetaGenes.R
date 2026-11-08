#!/usr/bin/env Rscript

#Functions
getBestIndexBy2SlopeLesser1stQuartilSlope <- function(p) {
  # A scan over k.max < 3 yields at most one slope, and the i/i+1 walk below
  # then reads past the end of y (y[2] is NA -> "missing value where TRUE/FALSE
  # needed", which aborts the script). With only k = 1..2 on the curve there is
  # no elbow to pick; the largest scanned k is the only informative answer and
  # the clamp after the caller bounds it by the distinct-point geometry anyway.
  if (nrow(p$data) < 3) { return(nrow(p$data)) }
  v = 2:nrow(p$data)
  y = p$data$y[v]-p$data$y[v-1]
  firstQ = summary(y)[2]
  c = NULL
  for(i in 1:(length(y)-1)){
    if(y[i]<=firstQ & y[i+1]<=abs(firstQ)){
      c = c(c,i+2)
    }
  }
  if (is.null(c)) { return(1) }
  index = which(p$data$y==min(p$data$y[c])) #de los elegidos, el de menor suma de cuadrado
  if (length(index) == 0) { return(1) }
  return(index[1])
}
## Collect arguments   -----------------------------------------------------------------------------------------------
args <- commandArgs(T)

## Default setting when no arguments passed --------------------------------------------------------------------------
if(length(args) < 5) {
  args <- c("--help")
}

## Help section
if("--help" %in% args) {
  cat('
      The R Script
 
      Arguments:
      --specie=someValue       - character, specie code e.g. mmu
      --input_file=someValue   - character, name for the input file
      --ouput_prefix=someValue - character, prefix for all output files
      --data_dir=someValue     - character, directory where saving temporal and output files 
      --kegg_dir=someValue     - character, location for KEGG data
      --sources_dir=someValue  - character, location for other R scritps
      --cutoff=someValue       - numerical, cutoff for the PCA Function (optional, default 0.3)
      --cluster=someValue      - character, clustering method (kmeans or hmclust),  (optional, default kmeans)
      --kclusters=someValue    - numerical, number of clusters for K-means/Mclust (optional, default calculated dinamically)
      --database=someValue     - character, name of the database (optional, default KEGG)

      --help                 - print this text
 
      Example:
      ./generateMetaGenes.R --specie="mmu" --input_file="Gene expression_matched.txt" --output_prefix="gene_expression" --data_dir="/home/rhernandez/Desktop/test/" --kegg_dir="/data/KEGG_DATA/ --sources_dir="/home/rhernandez/Desktop/workspace/paintomics/PaintomicsServer/src/common/bioscripts/"\n\n')
  
  q(save="no")
}

## Parse arguments (we expect the form --arg=value)
cat("generateMetaGenes.R - STEP 1. Parse arguments, ")
# Each argument is cut at its FIRST "=" only: the name is what comes before it,
# the value everything after it, character for character. --input_file and
# --output_prefix carry the omic's name, and a name may hold "=" or '"'. For an
# omic named "RNA=seq (x=2)" the job passes
#   --input_file=<job tmp dir>/RNA=seq (x=2)_matched.txt
# Splitting on every "=" kept only "<job tmp dir>/RNA", and removing every '"'
# changed a name like 'Prot "A"'; either way the file was not found and Step 2
# failed with an internal error. No quotes need removing: PathwayAcquisitionJob
# passes each argument as it is, without a shell, and a shell removes its own.
# The job passes "" for an option it leaves out (--kclusters, --database). An
# argument with no "=" is skipped, which drops those blanks as before.
# Base R only; checked under R 4.1.2, the R of the older servers.
# processReactomeData.R holds the same function; keep the two identical
# (test_r_arguments_keep_equals checks it).
parseArgs <- function(x) {
  x <- sub("^--", "", x[grepl("=", x, fixed = TRUE)])
  at <- regexpr("=", x, fixed = TRUE)
  values <- substring(x, at + 1L)
  names(values) <- substr(x, 1L, at - 1L)
  as.list(values)
}
args <- as.data.frame(parseArgs(args), stringsAsFactors=F)

#args <- data.frame(specie="ath",
#                   input_file="Gene expression_matched.txt",
#                   output_prefix="res",
#                   data_dir="/home/tian/Downloads/",
#                   kegg_dir='/home/tian/database/KEGG_DATA/',
#                   sources_dir='/home/tian/paintomics/paintomics4/PaintomicsServer/src/common/bioscripts',
#                   stringsAsFactors = F,
#                  cluster="kmeans")

## cutoff default
if(is.null(args$cutoff)) {
  # use mean(apply(data,1,var))*1.3  when B2GScore was not run
  args$cutoff <- 0.3
}
## cutoff default
if(is.null(args$cluster)) {
  args$cluster <- "kmeans" #default
}

if(is.null(args$database)) {
  args$database <- ""
} else {
  args$database <- paste("_", tolower(args$database), sep="")
}

args$kegg_dir <- paste0(args$kegg_dir, "current/", args$specie, "/gene2pathway",args$database, ".list", sep="")


# LOAD DEPENDENCIES   --------------------------------------------------------------------------------------------
cat("STEP 2. Load dependencies, ")
setwd(args$sources_dir)
source("PCA2GO.2.R")
source("PCA-GENES.R")

# parameters for PCA on genes of a GO
PCA2GO.fun = "PCA2GO.2"  # change to PCA2GO.2 when  B2GScore was not run
sel = "single%"


#LOAD DATA    ---------------------------------------------------------------------------------------------------
cat("STEP 3. Load input data, ")
#dir.create(args$data_dir, showWarnings = FALSE)
setwd(args$data_dir)
# Read the reference file
genes2pathway <- data.frame(read.table(file=args$kegg_dir, header=FALSE, sep="\t", quote="", comment.char="", as.is=TRUE))
# genes2pathway <-data.frame(lapply(genes2pathway, function(v) {
#   # if (is.character(v)) return(tolower(v))
#   # else return(v)
#   return(v)
# }))

# Lower gene names only
genes2pathway[,1] <- tolower(genes2pathway[,1])

# Read the input file
# Column layout, written by Job.py -- both the gene based writer and the
# compound based one use the same shape:
#
#   1 inputName | 2 featureName | 3 featureID | 4 matchingDB | 5..(4+n) values | last relevance flag
#
# Example (n = 6 conditions, so 11 columns):
# ENSMUSG00000000001	Gnai3	14679	KEGG	0.01523	0.01042	0.04686	0.01663	0.04748	0.04169	0
#                                                                                                 ^ relevance flag
#
# comment.char="" is load-bearing: column 2 is a display name straight out of
# the organism database, and real gene symbols contain '#' (potato ptt#2-1,
# human mt-GrpE#1, fly CG#6450). Under R's default comment.char="#" the rest of
# such a line is discarded, the row loses columns, and read.table aborts the
# whole metagenes phase with "line N did not have M elements" -- which surfaces
# to the user as step 2 failing outright for that organism.
input_data <- read.table(file=args$input_file, header=FALSE, sep="\t", quote="", comment.char="")

# CANONICALISE ROW ORDER ------------------------------------------------------
# The writer of <omic>_matched.txt (Job.py) emits its rows in an order that is
# not stable between runs of the same job on the same input: the file's md5
# changes run to run while the md5 of its *sorted* lines does not. Everything
# below used to inherit that order, and two effects turned it into a different
# answer rather than just a different file:
#
#   1. The deduplication on the next line keeps whichever copy of a repeated
#      feature ID happens to come FIRST. Repeated IDs are not rare and they do
#      not carry the same values -- measured on the bundled mmu single-condition
#      example, 193 of the IDs appear more than once and all 193 disagree about
#      the value. "First in the file" is therefore a coin flip that silently
#      rewrote 193 gene values per run.
#   2. Even with identical values, the per-pathway submatrix handed to PCA
#      inherits this order, and the eigen decomposition of a permuted covariance
#      matrix differs in its last bits. The single%-criterion in PCA2GO.2 counts
#      components above a threshold, so those last bits flip how many metagenes
#      a pathway contributes at all: measured 290 to 296 metagenes across 8
#      permutations of one 6-condition input.
#
# The two together made the same job produce a different Step-4 picture every
# run: 12 permutations of one real matched.txt gave 12 different cluster
# memberships and 8 different cluster-size signatures.
#
# Sorting here fixes it for any writer, which is why it belongs in the script
# and not in Job.py. The key is the lowercased feature ID -- the same value the
# deduplication and the row names below key on -- with every remaining column
# appended as a tie-break, so two rows sharing an ID resolve to the
# lexicographically smallest one deterministically instead of by arrival order.
# method = "radix" pins C-locale collation: the default for character vectors is
# the collation locale, which would make the answer depend on the machine's LANG.
# Sorting reorders whole rows, so column identity within a row is untouched, and
# feature_ids below is derived from the sorted frame.
sort_keys <- c(list(tolower(input_data[[3]])),
               lapply(input_data, function(col)
                 if (is.character(col)) tolower(col) else col))
input_data <- input_data[do.call(order, c(sort_keys, list(method = "radix"))), ,
                         drop = FALSE]

# Remove duplicates
# TODO: we take the first match after the canonical sort above, which is
# reproducible but still arbitrary between the copies; averaging them is the
# open question, and it is a change of statistic rather than of determinism.
# Deduplicate on the *lowercased* ID because that is what becomes row.names
# below; two IDs differing only in case would otherwise reach data.frame() as
# duplicate row names and abort the run.
input_data <- input_data[!duplicated(tolower(input_data$V3)),]

# Drop the trailing relevance flag. It is a 0/1 significance indicator, NOT a
# condition. Commit 6c7a7934 ("Add multi-condition support for PaintOmics4")
# appended it to the mapping files and this script was never updated, so it has
# been read as an extra condition ever since. A binary column sitting next to
# log-ratios of order 1e-2 dominates the per-pathway PCA: PC1 degenerates into
# "how many genes of this pathway are significant", every metagene comes out as
# a flat line with a spike at the last point, and the cluster thumbnails then
# differ only in the height of that spike -- which is why they all looked alike.
#
# Job.py always writes exactly one flag column (isRelevant() is called with no
# conditionIndex, so it collapses a per-condition list to a single boolean).
# test_metagenes_clustering.py pins that contract; if it ever changes, that test
# fails and points back here.
n_conditions <- ncol(input_data) - 5
if (n_conditions < 1) {
  stop("Input file has no condition columns (only ", ncol(input_data),
       " columns): ", args$input_file)
}
value_cols <- 5:(4 + n_conditions)
# Adapt input data to a data.frame object
feature_ids <- tolower(input_data[,3])
if (args$database == "") {
  feature_ids <- paste(args$specie, ":", feature_ids, sep="")
}
input_data <- data.frame(input_data[, value_cols, drop=FALSE], row.names=feature_ids)

#genes2pathway[which(genes2pathway[,1] %in% rownames(input_data)),]
# GET METAGENES  ------------------------------------------------------------------------------------------------
cat("STEP 4. Obtaining metagenes, ")
# Pick the estimator by what the design can support, and say which one ran.
# PC1 across conditions needs at least two observations to have a direction of
# maximum variance at all; with one condition the pathway summary is a location
# statistic of its member genes. CENTROID2GO (PCA2GO.2.R) is that estimator and
# returns the identical shape, so nothing downstream branches on this choice.
if (n_conditions >= 2) {
  cat("PC1 across ", n_conditions, " conditions, ", sep = "")
  expression_GO <- get(PCA2GO.fun)(input_data, genes2pathway, var.cutoff = args$cutoff, fac.sel =  sel)
} else {
  cat("median centroid (single condition), ")
  expression_GO <- CENTROID2GO(input_data, genes2pathway, var.cutoff = args$cutoff)
}
if (is.null(expression_GO$X.sel)) {
  cat("No metagenes found for this database and input data. Exiting gracefully.\n")
  q(save="no", status=0)
}


# ADJUST GENE DIRECTION -----------------------------------------------------------------------------------------
##for each metagene
adjust.direction <- function (expression_GO) {
  metagenes <- expression_GO$X.sel
  for (i in 1:length(row.names(metagenes)) ) {
    cur_pathway_id <- unlist(lapply(strsplit(row.names(metagenes)[i], "_"), function(x) x[1]))
    #loadings indicates the contribution of each gene to PC1
    #The loading sign is not arbitrary. 
    #Positive loading indicates positive correlation of gene expression with the scores while negative loading indicates negative correlation.
    gene_loadings <- unlist(expression_GO$X.loadings[i])
    #select genes that contribute most in a given component as
    # abs(loading del gen)/sum(abs(loadings de todos los genes)
    nGenes <- length(gene_loadings)
    loadings_sum <- sum(abs(gene_loadings))
    has_positives<-0
    has_negatives<-0
    selected <- c()
    for(j in gene_loadings ){
      #If this value is greater than 1/total_genes, the gene is selected because it has a greater contribution 
      #than the value of contribution if all the same genes contribute together.
      if(abs(j)/loadings_sum > 1/nGenes){
        selected <- c(selected, j)    
        has_positives<- has_positives + ifelse(j > 0, 1, 0)
        has_negatives<- has_negatives + ifelse(j < 0, 1, 0)
      }
    }
    
    ## Change the direction for the metagene
    ##If most or all of the genes have a positive loading then
    if(has_positives > has_negatives ){
      #leave metagene as it is
      ##If most or all of the genes have a negative loading then invert metagene
    }else if(has_negatives > has_positives){
      metagenes[i,] <-metagenes[i,] * -1 
      ##If same number of negative and positive loadings then resolve
    }else if(has_negatives > 0 && has_positives > 0){
      has_positives <- sum(selected[selected>0])
      has_negatives <- abs(sum(selected[selected<0]))
      if(has_negatives > has_positives){
        ##If negative loadings genes are bigger then invert metagene
        metagenes[i,] <-metagenes[i,] * -1 
      }
    }
  }
  return(metagenes)
}
# The sign of a principal component is arbitrary, so the PCA branch has to
# orient each metagene against the loadings of the genes that built it. A median
# centroid is already expressed in the data's own units and direction -- there
# is no sign to resolve, and running the loading vote over the deliberately
# uniform loadings CENTROID2GO returns would be a no-op that only looks like a
# decision.
if (n_conditions >= 2) {
  metagenes <- adjust.direction(expression_GO)
} else {
  metagenes <- expression_GO$X.sel
}

# Canonicalise the metagene row order before anything clusters it.
#
# The rows arrive in the order the pathways appear in gene2pathway.list, so this
# is a second, independent exposure: regenerate the KEGG snapshot with the
# pathways in another order and the clustering moves, even though not one
# metagene value changed. Both clusterers are order-sensitive on ties --
# Mclust seeds EM from model-based hierarchical agglomeration, whose merges are
# resolved by row position when distances tie, and 1-D metagenes tie constantly;
# kmeans draws its starting centres by sampling rows.
#
# This must run AFTER adjust.direction, which pairs X.sel row i with
# X.loadings[[i]] positionally. Nothing below is positional: the thumbnails, the
# cluster column and write.table all address rows by name, and columns are not
# touched, so sorting cannot separate a pathway from its values.
metagenes <- metagenes[order(rownames(metagenes), method = "radix"), , drop = FALSE]

# CLUSTERIZE ----------------------------------------------------------------------------------------------------
data <- metagenes

library(cluster)
library(amap)
library(mclust) #new
library(factoextra) #new
dataScaled <- t(scale(t(data), center = T, scale = F)) #no do scaling with all subset

# Matrix the clustering actually runs on: each metagene centred across
# conditions (above) and then rescaled to unit length. For centred, unit-norm
# vectors ||x - y||^2 == 2 * (1 - pearson(x, y)), so plain Euclidean k-means on
# this matrix minimises exactly the pearson dissimilarity the script used to
# build with amap::Dist(). That is what a metagene profile wants: cluster on the
# *shape* of the response across conditions, independent of amplitude.
#
# This replaces `dist.res <- Dist(dataScaled, method = "pearson")` fed straight
# into stats::kmeans(). kmeans() opens with as.matrix(x), and as.matrix.dist
# expands a dist object into the full n x n distance matrix -- so kmeans was
# clustering the *rows of the distance matrix*, i.e. n points in n dimensions,
# under Euclidean distance. The pearson metric was never applied, the call was
# O(n^2) in time and memory, and the partitions came out badly unbalanced: on
# the example dataset k = 6 gave cluster sizes like 1, 5, 11, 36, 140, 166.
# Clusters that small routinely have no pathway left after Step 3's p-value
# filter, and the Step 3 panel only renders clusters that still own a visible
# pathway (PA_Step3Views.js builds CLUSTERS from surviving nodes), so asking for
# 6 clusters could display 4.
#
# Nothing needs the pairwise distance matrix any more, so it is no longer built:
# it was O(n^2) memory for a value that is now unused.
rowNorms <- sqrt(rowSums(dataScaled^2))
# A metagene that is flat across every condition has zero norm. Leave it at the
# origin rather than dividing by zero and seeding the matrix with NaN, which
# kmeans() rejects outright.
rowNorms[!is.finite(rowNorms) | rowNorms == 0] <- 1
dataForClustering <- dataScaled / rowNorms

# Single condition: undo the two transformations above, because both of them are
# statements about shape across conditions and there is no such axis here.
# Centring a one-column matrix per row sends every value to exactly 0, and the
# unit-norm step then divides 0 by the rowNorms guard -- the whole matrix
# collapses onto a single point and any partition of it is arbitrary. With one
# condition the amplitude IS the profile, so cluster the metagene values
# themselves. This keeps "cluster metagenes by their response" true in both
# regimes; only the meaning of "response" narrows from a shape to a magnitude.
if (n_conditions < 2) {
  dataScaled <- data
  dataForClustering <- data
}

# k-means in one dimension is the wrong tool: Lloyd's algorithm has no exact
# optimum, seeds randomly, and its elbow scan below assumes a cloud with more
# structure than a single axis carries. mclust is already a supported branch of
# this script, handles 1-D natively, chooses the number of components by BIC and
# reports per-component variance, so single-condition jobs are routed to it.
# (An exact 1-D dynamic-programming k-means would also be correct, but that means
# adding Ckmeans.1d.dp to every deployment; mclust is already a dependency.)
if (n_conditions < 2 && args$cluster == "kmeans") {
  cat("Single condition: clustering with mclust in 1-D instead of k-means. ")
  args$cluster <- "mclust"
}

# k-means seeds its centres from a random sample of rows. With no fixed seed the
# same job produced a different partition on every run, so the thumbnails and
# the cluster column of the .tab file disagreed between Step 2 and any later
# recomputation of the same omic.
set.seed(149)

if(is.null(args$kclusters) || args$kclusters == "dynamic") {
  if (nrow(dataScaled) < 3) {
    args$kclusters <- 1
  } else {
    ## cutoff default

    # Compute pairwise distance matrices

    k.max <- round(sqrt(length(row.names(dataScaled))/2)) + 1
    if (k.max < 2) k.max <- 2

    # The elbow scan below runs kmeans for EVERY k up to k.max, so it aborts on
    # "more cluster centers than distinct data points" long before the clamp
    # after this block can help. The geometry matters with two conditions: the
    # centred, unit-norm rows occupy exactly 2 distinct locations, so any
    # k.max > 2 kills the scan. Bound the scan by the distinguishable points
    # (same rounding rationale as the clamp below).
    distinctPoints <- nrow(unique(round(dataForClustering, 10)))
    if (is.finite(distinctPoints) && k.max > distinctPoints) k.max <- distinctPoints

    if (k.max < 2) {
      # A single distinguishable profile cannot be partitioned; fviz_nbclust
      # and Mclust both require k.max >= 2, so decide directly.
      cat("Only", distinctPoints, "distinct metagene profile(s); using 1 cluster. ")
      args$kclusters <- 1
    } else if(args$cluster=="kmeans"){
      # Check best cluster using WSS. Run the elbow over the same matrix the
      # final clustering uses -- it previously scanned dataScaled while the
      # clustering ran on the distance matrix, so the chosen k described a
      # different space than the partition it was chosen for.
      p = fviz_nbclust(x = dataForClustering, FUNcluster = stats::kmeans, method = c("wss"),
                       k.max = k.max, nstart = 25, iter.max = 500, verbose = TRUE) +
        labs(title = "Optimal number of clusters")
      args$kclusters <- getBestIndexBy2SlopeLesser1stQuartilSlope(p)
      p <- p + geom_vline(xintercept = args$kclusters, linetype = 2)
      ggsave(plot = p, filename=paste0(args$output_prefix,"_elbow.png"), width = 15, height = 6, dpi = 200, units = "cm")

    }else{
      # Compute clusters using Mclust (ML)
      fit <- Mclust(dataForClustering, G = 1:k.max)
      args$kclusters <- fit$G
    }
  }
} else {
  args$kclusters = as.integer(args$kclusters)
}

# Clamp k to what the clustering can actually express. stats::kmeans() rejects
# k >= nrow(x) ("number of cluster centres must lie between 1 and nrow(x)") and,
# with nstart > 1, k above the number of *distinct* rows ("more cluster centers
# than distinct data points"). Either one aborts the script and takes the whole
# omic-and-database pair down with it, which the caller surfaces as a failed
# job. A secondary database (a MapMan or Reactome subset matching a handful of
# pathways) reaches that easily as soon as a user drags the Step 3 slider up.
args$kclusters <- as.integer(args$kclusters)
if (is.na(args$kclusters) || args$kclusters < 1) args$kclusters <- 1
nMetagenes <- nrow(dataForClustering)
# unique() on doubles compares bit patterns, which is not the question being
# asked here -- the clamp wants to know how many *distinguishable* points there
# are. With exactly two conditions the centred, unit-norm rows are algebraically
# only ever (+1/sqrt2, -1/sqrt2) or its negation, yet floating-point noise in the
# division above leaves them bitwise distinct: measured 61 "distinct" rows for
# 500 metagenes when there are 2. The clamp therefore never fired, k-means was
# handed k = 17 for a cloud occupying 2 locations, and it returned 17 clusters
# that mean nothing rather than erroring. Rounding first makes the count reflect
# the geometry.
maxClusters <- min(nrow(unique(round(dataForClustering, 10))), nMetagenes - 1L)
if (args$kclusters > maxClusters) {
  cat("Requested", args$kclusters, "clusters but only", nMetagenes,
      "metagenes are available; using", max(maxClusters, 1L), ". ")
  args$kclusters <- maxClusters
}
if (args$kclusters < 1) args$kclusters <- 1

if(nMetagenes < 2 || args$kclusters < 2){
  # Nothing to partition. Worth special-casing rather than leaving to kmeans():
  # it cannot express k = nrow(x) at all, so a database matching a single
  # pathway used to abort here even though the answer is trivially one cluster.
  # Both member names are supplied so clust.centroid() works for either method.
  args$kclusters <- 1
  singleton <- setNames(rep(1L, nMetagenes), rownames(dataForClustering))
  clusters <- list(cluster = singleton, classification = singleton)
}else if(args$cluster=="kmeans"){
  # Re-seed here rather than relying on the set.seed above. The elbow scan
  # between the two consumes a number of random draws that depends on the data
  # (k.max, and how many restarts each k needs to converge), so the RNG state
  # reaching this call was a function of the input size. Seeding immediately
  # before the draw that matters makes the final partition depend on the data
  # alone, and makes this call reproducible when k is passed in explicitly and
  # the elbow scan never runs at all.
  set.seed(149)
  # nstart = 100: with a fixed seed any nstart is reproducible, but reproducible
  # is not the same as stable -- a poor local optimum reached identically every
  # time is still a poor answer, and it moves under any small change to the
  # input. Measured over 12 seeds on a 6-condition example (296 metagenes,
  # k = 5): nstart = 1 reached 6 different partitions and a tot.withinss spread
  # of 55.896 to 56.630; nstart = 25 reached 2 partitions (55.896 to 55.982);
  # nstart = 100 and nstart = 200 both reached the same single partition at the
  # best value seen, 55.896. 100 is where the optimum stops moving, and it costs
  # 10 ms per call at this size, so it is taken rather than 25.
  clusters <- stats::kmeans(dataForClustering, centers = args$kclusters,
                            iter.max = 500, nstart = 100)
}else{
  clusters <- fit <- Mclust(dataForClustering, G = args$kclusters)
}




# GENERATE THE METAGENES IMAGES-----------------------------------------------------------------------------------
cat("STEP 5. Generate output files...\n")
prev_pathway_id <- ""

# function to find medoid in cluster i
#
# drop = FALSE is required. R drops a single-row matrix subset to a plain
# vector, and colMeans then aborts with
#   Error in colMeans(data[ind, ]) : 'x' must be an array of at least two
#   dimensions
# which halts the whole script. Clusters of exactly one gene are common, so
# this took out metagene generation for an entire omic-and-database pair at a
# time: on the example job it killed Gene expression for all of KEGG, and the
# pathway panel then reported "No data for this pathway" for every KEGG
# pathway even where the omic had matched features.
clust.centroid = function(method, data, clusters, i) {
  if(method == 'kmeans'){
    ind = (which(clusters$cluster == i))
    colMeans(data[ind, , drop = FALSE])
  }else{ #mclust
    ind = (which(clusters$classification == i))
    colMeans(data[ind, , drop = FALSE])
  }
}

minMax <- range(data)
# GENERATE THE METAGENES IMAGES-----------------------------------------------------------------------------------
for (i in 1:args$kclusters){
  #GET THE PATHWAY IDS FOR CURRENT CLUSTER
  if(args$cluster=="kmeans"){
    pathway_ids <- names(which(clusters$cluster==i))
  }else{
    pathway_ids <- names(which(clusters$classification==i))
  }
  #GET THE VALUES FOR THESE PATHWAYS
  # drop = FALSE again: for a cluster holding a single pathway the subset
  # collapses to a vector, and as.matrix() then turns it into an
  # nConditions x 1 column rather than a 1 x nConditions row. row.names() would
  # be the condition names, so the length > 1 test below took the multi-line
  # branch and plotted values[1,] -- one number -- instead of the single
  # pathway's profile.
  values <- as.matrix(dataScaled[pathway_ids, , drop = FALSE])
  #CREATE THE PNG
  png(paste(args$output_prefix, "_cluster_", i, args$database, ".png", sep=""), height = 150, width = 150)
  par(mai = rep(0, 4), mar = rep(0.8, 4))

  # An empty cluster makes range() return c(Inf, -Inf), and plot() then aborts
  # with "need finite 'ylim' values", which would kill the script here -- after
  # some thumbnails exist but before the .tab file is written. Emit an empty
  # framed panel instead: the client requests one image per cluster index and a
  # missing file shows up as a broken image.
  if(nrow(values) == 0){
    plot.new()
    title(main = "0 metagenes")
    box()
    dev.off()
    next
  }
  minMax <- range(values)

  if(ncol(values) == 1){
    # One condition: a polyline through a single x position draws nothing at
    # all, so the thumbnail would come out blank. The honest picture of a
    # one-dimensional cluster is where its members sit on the value axis, so
    # plot them as points with the cluster centroid marked. range() of a
    # one-member cluster is a single number and would give a degenerate xlim.
    if (diff(minMax) == 0) minMax <- minMax + c(-0.5, 0.5)
    stripchart(as.numeric(values), method = "jitter", jitter = 0.15, pch = 16,
               col = "gray60", xlim = minMax, axes = FALSE,
               main = paste(length(pathway_ids), "metagenes"))
    abline(v = 0, lty = 3, col = "gray40")
    abline(v = clust.centroid(args$cluster, dataScaled, clusters, i),
           col = "red", lwd = 2)
    box()
  }else{
  if(length(row.names(values)) > 1){
    #Plot first cluster
    plot(as.matrix(values[1,]), type="l", col="gray88", main=paste(length(pathway_ids), "metagenes"), axes=F, xlab=NULL, ylim = minMax)
    #Plot remaining clusters (if any)
    for (n in 2:length(row.names(values)) ) {
      lines(as.matrix(values[n,]), type="l", col="gray88")
    }
    #Plot centroid if multiple lines
    lines(clust.centroid(args$cluster, dataScaled, clusters, i), type="l", col="red", lwd=2)
  }else{
    plot(as.matrix(values), type="l", col="red", main=paste(length(pathway_ids), "metagenes"), axes=F, xlab=NULL, ylim = minMax)
  }
  abline(h =0)
  box()
  }
  
  dev.off()
}

#Add cluster info to table
metagenes <- as.data.frame(data)
for (i in 1:args$kclusters){
  #GET THE PATHWAY IDS FOR CURRENT CLUSTER
  if(args$cluster=="kmeans"){
    pathway_ids <- names(which(clusters$cluster==i))
  }else{
    pathway_ids <- names(which(clusters$classification==i))
  }
  metagenes[pathway_ids, "cluster"] <- i
}
#Update the name for the rows
rownames(metagenes) <- gsub("_", "\t", gsub("path:", "", rownames(metagenes)))
#rownames(metagenes) <- gsub("_*", "", gsub("path:", "", rownames(metagenes)))

#Save table to file
if (args$database == "") {
  output_file = paste(args$output_prefix, "metagenes.tab", sep="_")
} else {
  output_file = paste(args$output_prefix, "metagenes", paste0(substring(args$database, 2), ".tab"), sep="_")
}
write.table(metagenes[, c(ncol(metagenes), 1:(ncol(metagenes) - 1))], file=output_file, quote = FALSE, sep="\t", col.names = FALSE)
