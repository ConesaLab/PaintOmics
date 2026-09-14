# What the AI does

PaintOmics has three separate AI features. They share a gateway and a set of
rules, but they do different jobs at different points in the analysis, and each
one can be switched off independently by whoever runs the server.

| Feature | Where | What it does |
|---|---|---|
| **[Input conversion](ai-input-converter.md)** | Step 1, on a file you pick | Turns a file that is not in PaintOmics' format — a DESeq2 table, a MaxQuant output, a multi-sheet workbook — into one that is, in your browser, and shows you the script it used. |
| **[Compound disambiguation](8_step_by_step.md#compound-disambiguation)** | Step 2, **Choose for me** | Picks the most likely KEGG compound for each ambiguous metabolite name, using your organism and your experiment description. |
| **[Interpretation](ai-interpretation.md)** | Step 3, **AI Interpret**; Step 4, **Walk** | Walks the KEGG, Reactome and OmniPath interactions with your values on them and writes a checked, cited Results section of what the walk shows. |

## The rules they all follow

**You are told where your data goes, by name.** Section 2 of the upload form
names the gateway the model runs on — by default `llm.iiia.es`, operated by
IIIA-CSIC, the Artificial Intelligence Research Institute of the Spanish
National Research Council, on hardware in Spain — and the **Where your data
goes** link opens a full statement of what leaves the server. Read it once, and
read this alongside it, because on one point the interface is narrower than the
code: feature names and **the measured values with their column labels** of
every node the walk reaches or shows are sent to the model, together with your
experiment description, and the chat can ask for the values of any gene it can
name **anywhere in your upload**. What is never sent is your uploaded files as
files, features that failed to map to any identifier, and anything about your
account.

Searches also reach NCBI PubMed and Europe PMC. The query is composed by the
model, not assembled from a fixed template, and the prompt it composes from
contains your experiment description — so treat that description as text that
may reach a third-party search API, not only the gateway.

**The model never grades its own work.** Every AI output is checked by
something deterministic before you see it. A converted file must pass the same
format validator your own upload would. In the interpretation, every quoted
value must match your upload, every leg must be an interaction a database
draws, and every cited paper must have been retrieved and read and must name
the genes of the claim; a statement that fails is rewritten once and then
dropped.

**Nothing is presented as fact.** Every interpretation ends with the line *"Drafted by
a large language model, not by a person. Check every claim and every citation
against the sources before relying on it."* — inside the same block as the
text, so copying the write-up copies the attribution.

**Its work is inspectable.** The converter shows the Python it wrote and the
validator's verdict; the interpretation lists the legs as the agent walks them,
and shows every leg, statement and dropped statement with its reason.

## What is on by default

A PaintOmics server decides this, so the answer depends on where you are
running.

| Setting | Ships as | Controls |
|---|---|---|
| `AI_INTERPRETATION_ENABLED` | **on** | The interpretation, the follow-up chat, the Step 4 **Walk** column and **Draft this for me**. |
| `AI_COMPOUND_SUGGESTIONS_ENABLED` | **on** | Step 2's **Choose for me**. Needs `AI_INTERPRETATION_ENABLED` as well. |
| `AI_INPUT_CONVERTER` | **off** | The input converter. It ships inert because it spends the same gateway quota as the reports. |

All of them also need an API key for the configured provider. If a server has
the feature enabled but no key, the interpretation never starts and the
progress bar sits at "Not started" — see [the failure
states](ai-interpretation.md#when-it-does-not-work).

!!! note "Consent"
    There is no consent checkbox on the form. Submitting a job through the
    upload form records consent for that job, and the server re-checks it on
    every request that would send anything outward — starting the
    interpretation, a follow-up question, a pathway walk. A job whose
    record says otherwise is refused. What replaces the checkbox is the
    **Where your data goes** statement, shown before you submit rather than
    buried in a tick-box.

## What to leave out of an AI job

Do not put personally identifiable information, protected health information,
or identifiable sequence reads into a job you intend to interpret with the AI.
The values and their condition labels are sent to the gateway; condition labels
in particular are free text you chose, so a column named after a patient is a
column that leaves the server.
