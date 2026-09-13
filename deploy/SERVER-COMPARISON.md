# paintomics.org (Drago VM) vs paintomics.uv.es — measured 2026-09-07

Measured on both live machines within a few minutes of each other, right after the DNS cutover.

## Hardware

| | paintomics.uv.es | paintomics.org (Drago VM) |
|---|---|---|
| vCPU | 6, QEMU Virtual CPU 2.5+ | 20, Intel Xeon (Granite Rapids) |
| RAM | 7 GB | 49 GB |
| Disk for data | 146 GB total, 56 GB free | 1 TB Cinder volume, 875 GB free |

## Measured speed

| test | paintomics.uv.es | Drago VM | verdict |
|---|---|---|---|
| single-core CPU (fixed Python integer + float loop) | 0.92 s | **0.81 s** | VM ~12% faster per core |
| disk write, 512 MB with fdatasync | 69.5 MB/s | **304 MB/s** | **VM 4.4x faster** |
| AI interpretation, end to end, median | **305 s** (n=83) | 355 s (n=22) | see caveat below |

## Reading these numbers

**A single job is only slightly faster on the VM.** PaintOmics runs uWSGI with `processes = 1`
and an in-process job queue, so one job gets one core. A 12% faster core is what that job sees.

**Disk is where the VM actually wins.** Pathway acquisition, KEGG/Reactome reads, uploads and
job output are file-heavy, and the VM's volume writes 4.4 times faster. Expect the I/O-bound
phases of a job to be noticeably quicker, not the compute-bound ones.

**The AI interpretation figure does not measure the server.** It is wall-clock from `createdAt`
to `updatedAt` on the AI record, and most of that is waiting on the CSIC language-model gateway,
which is the same external service for both machines. The VM's median is *higher*, but its 22
records are test runs, several made while the machine was simultaneously building images and
downloading pathway data; production's 83 are real user traffic on an idle box. The two samples
are not comparable, and neither is evidence about server speed. Do not quote this row as a
performance result.

**The real gain is headroom, not latency.** 7x the RAM and 3x the cores mean the VM degrades far
less when several people run jobs at once, and 875 GB free versus 56 GB removes the disk ceiling
that production was approaching. Under load the difference should be much larger than 12%.

## How to re-measure

    # single core
    python3 -c "import time;t=time.perf_counter();s=0
    for i in range(3000000): s=(s+i*i)%99991
    print('%.2f s' % (time.perf_counter()-t))"
    # disk
    dd if=/dev/zero of=/tmp/.bench bs=1M count=512 conv=fdatasync; rm -f /tmp/.bench

A fair job-level comparison needs the same input run on both machines with each otherwise idle;
that has not been done. Until it is, the honest summary is: same order of magnitude per job,
much faster disk, far more headroom.
