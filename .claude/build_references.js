// Builds docs/References.docx. Edit REFS and re-run to update.
const fs = require("fs");
const path = require("path");
const {
  Document, Packer, Paragraph, TextRun, HeadingLevel, ExternalHyperlink,
  AlignmentType, BorderStyle,
} = require("docx");

const OUT = process.argv[2];

const SECTIONS = [
  {
    title: "1. Single-scan, per-element fault detection",
    refs: [
      {
        title: "A scan-specific quality control acquisition for clinical whole-body (WB) MRI protocols",
        cite: "Physics in Medicine & Biology, 2024. doi:10.1088/1361-6560/ad4195",
        url: "https://iopscience.iop.org/article/10.1088/1361-6560/ad4195",
        status: "Method section summarised",
        key: "Computes a signal-to-background ratio (SBR) for each element image and for the sum-of-squares image, and flags an element whose SBR is far below the combined image. Within-scan, no stored baseline. Over 892 exams: 100% recall, 80% precision. They only investigated an element after it was low in several exams (e.g. 3 in a row).",
        rel: "Closest published match to our current detector. Supports single-scan detection of dead/near-dead elements; also hints that persistence across scans (coil history) is used in practice.",
      },
      {
        title: "Receive coil quality assurance procedure and automated analysis for ViewRay MRIdian MR-Linac",
        cite: "Kaza et al., Journal of Applied Clinical Medical Physics, 2024. doi:10.1002/acm2.14275",
        url: "https://pmc.ncbi.nlm.nih.gov/articles/PMC10930007/",
        status: "Abstract summary",
        key: "Spin-echo acquisition reconstructed per element; automated analysis flags faulty elements against SNR and uniformity thresholds, removing observer bias.",
        rel: "Example of automated per-element QA with fixed thresholds.",
      },
      {
        title: "Automatic Method for RF Coil Assessment in Clinical MRI",
        cite: "AAPM abstract",
        url: "http://amos3.aapm.org/abstracts/pdf/68-19039-233355-89252.pdf",
        status: "Search summary only",
        key: "Evaluates each channel in a 3D image space for a faster, less subjective check; demonstrated on a GE torso coil with one failed receiver.",
        rel: "Supports using whole-volume (3D) per-element maps, as we do with the MPRAGE raw data.",
      },
      {
        title: "Basic Principles of and Practical Guide to Clinical MRI Radiofrequency Coils",
        cite: "RadioGraphics (RSNA). doi:10.1148/rg.210110",
        url: "https://pubs.rsna.org/doi/abs/10.1148/rg.210110",
        status: "Search summary only",
        key: "Case of a 19-channel head/neck coil where a defective element was masked by the other elements; mean and max SNR of the combined image missed it, the vendor coil QA caught it.",
        rel: "Shows why combined-image metrics are not enough and per-element analysis is needed.",
      },
    ],
  },
  {
    title: "2. Noise covariance matrix (NCM) for coil QA",
    refs: [
      {
        title: "Use of the noise covariance matrix in array coil quality assurance",
        cite: "Tunnicliffe, Graves & Robson, Proc. ISMRM 19 (2011) #4548",
        url: "https://cds.ismrm.org/protected/11MProceedings/PDFfiles/4548.pdf",
        status: "Read in full",
        key: "NCM from noise-only data (RF off). A >15% SNR drop was found when (1) diagonal (noise power) varies by >35%, or (2) the largest off-diagonal is >25% of the largest diagonal. If elements are not equally loaded, use an 80% diagonal threshold, which catches only non-functional elements. Coupling failures can cost 18% SNR while being invisible in per-element images. Alternative: characterise the NCM at commissioning and track it.",
        rel: "Main basis for adding NCM features (noise level and noise coupling per element) to our detector. Its single-scan thresholds are larger than our 10-30 faults (11-14%).",
      },
      {
        title: "The NMR phased array",
        cite: "Roemer et al., Magnetic Resonance in Medicine 16:192-225 (1990)",
        url: "https://doi.org/10.1002/mrm.1910160203",
        status: "Cited by Tunnicliffe; not read",
        key: "Foundational phased-array paper: defines the noise covariance matrix and optimal (noise-weighted) combination.",
        rel: "Theory behind noise correlation between elements.",
      },
      {
        title: "Transmission line effects on the coil noise correlation matrix in MRI",
        cite: "Brown & Wang, 2008 (Weill Cornell)",
        url: "https://radiology.weill.cornell.edu/research/publications/transmission-line-effects-coil-noise-correlation-matrix-mri",
        status: "Search summary only",
        key: "Extends the noise correlation formula to include transmission-line effects and preamplifier input mismatch.",
        rel: "Physics link between preamp mismatch (our 'dematch' fault) and noise correlation.",
      },
      {
        title: "Noise covariance with overlap and preamplifier decoupling (Bosma formula analysis)",
        cite: "Gapais, Luong, Almokdad, Georget & Amadon, Proc. ISMRM (2022) #1537",
        url: "https://cds.ismrm.org/protected/22MProceedings/PDFfiles/1537.html",
        status: "Search summary only",
        key: "Combining overlap with preamplifier decoupling can sharply change noise correlation between elements.",
        rel: "Suggests a dematched preamp should change an element's noise coupling to its neighbours.",
      },
      {
        title: "Magnetic resonance imaging apparatus and abnormality detection method",
        cite: "Canon patent US 11041930",
        url: "https://patents.justia.com/patent/11041930",
        status: "Search summary only",
        key: "Locates receive-path faults by comparing noise levels with the coil element connected vs not connected to the ADC, and uses preamp gain to tell cases apart.",
        rel: "Industry use of per-channel noise level as a fault indicator.",
      },
      {
        title: "Prediction of MRI hardware failures based on image features using ensemble learning",
        cite: "Kuhnert et al. (FAU Erlangen), arXiv:2001.01213 (2020)",
        url: "https://arxiv.org/abs/2001.01213",
        status: "Paper summary read",
        key: "20-channel Siemens head/neck coils classified normal vs broken. Per-channel features: noise level, SNR, body-coil/head-coil signal ratio, SNR vs isocentre signal; plus the whole NCM fed to a CNN. Stacked ensemble: 94% F-score, 99% accuracy. Trained on many coils with labels; no feature importance reported.",
        rel: "Shows that the NCM pattern carries fault information, but needs a large labelled set of coils (we have one coil, two faults). Labels whole coils, not individual elements.",
      },
    ],
  },
  {
    title: "3. Longitudinal (coil history) QA",
    refs: [
      {
        title: "A fast quality assessment and follow-up of transmit and receive coil paths",
        cite: "Proc. ISMRM (2018) #4221",
        url: "https://cds.ismrm.org/protected/18MProceedings/PDFfiles/4221.html",
        status: "Abstract read",
        key: "7T, 32-channel coil: noise-only + excited EPI give per-channel noise and SNR, tracked over time. Channels differ in noise because of coupling, but each is stable over time; no explicit flag threshold.",
        rel: "Evidence for option D (coil history): natural element-to-element differences are large but stable, so changes over time are easier to see than outliers in one scan.",
      },
      {
        title: "Siemens Healthineers local coil QA patent",
        cite: "US 11073588",
        url: "https://patents.google.com/patent/US11073588",
        status: "Search summary only",
        key: "Coil QA results (combined and per-element) are checked against reference values or specification parameters to give pass/fail.",
        rel: "Vendor approach relies on stored reference values; relevant to option D.",
      },
      {
        title: "ACR phantom image quality with inactive coil elements",
        cite: "Medical Physics, 2014. doi:10.1118/1.4889217",
        url: "https://zendy.io/title/10.1118/1.4889217",
        status: "Search summary only",
        key: "8-channel and 20-channel arrays: switching elements off dropped SNR by up to 57% and uniformity by 34%; ACR uniformity limits failed with 2 inactive elements.",
        rel: "Quantifies image-quality impact of dead elements.",
      },
      {
        title: "Continuous MRI coil quality control using clinical imaging data",
        cite: "Duke University master's thesis (DukeSpace)",
        url: "https://dukespace.lib.duke.edu/items/b4fb7a3b-7297-40f9-a116-b241592db0d2",
        status: "Search summary only",
        key: "Reconstructs individual coil-element images from routine localizer raw data and tracks corrected per-element SNR across scanners; a threshold caught three faulty coils up to three weeks before clinical signs.",
        rel: "Strong precedent for option D: routine scans as free, continuous per-element monitoring.",
      },
      {
        title: "Noise characteristics of clinical images before coil failure (machine learning)",
        cite: "Proc. ISMRM (2025) #4157",
        url: "https://archive.ismrm.org/2025/4157.html",
        status: "Abstract read",
        key: "Noise characteristics in clinical images acquired shortly before QA detected a coil failure differ from those of healthy coils; ML separates the two. Features not specified.",
        rel: "Supports looking beyond signal level (noise behaviour) for early or partial faults.",
      },
    ],
  },
  {
    title: "4. Coil physics: detuning, mismatch, preamp decoupling (not yet reviewed)",
    refs: [
      {
        title: "Preamplifier noise coupling in receive arrays",
        cite: "Proc. ISMRM (2012) #2689",
        url: "https://cds.ismrm.org/protected/12MProceedings/PDFfiles/2689.pdf",
        status: "Not yet reviewed",
        key: "Returned by search on preamp noise coupling; topic: preamp noise fed back through mutual impedance adds to the NCM diagonal.",
        rel: "Possible explanation of noise increase on a dematched element.",
      },
      {
        title: "Loop mistuning tolerance and preamp noise matching",
        cite: "Proc. ISMRM (2011) #1866",
        url: "https://cds.ismrm.org/protected/11MProceedings/PDFfiles/1866.pdf",
        status: "Not yet reviewed",
        key: "Reported in search: a loop can be mistuned by about ±4 MHz before the preamp no longer sees its optimal noise-match impedance.",
        rel: "Relevant to how large an 'untune' fault must be to show up.",
      },
      {
        title: "Matching-network noise figure and preamp decoupling",
        cite: "Proc. ISMRM (2023) #3905",
        url: "https://cds.ismrm.org/protected/23MProceedings/PDFfiles/3905_TvPTFaQcS.html",
        status: "Not yet reviewed",
        key: "Noise figure of a matching network estimated as F ≈ 1 + R11/Rc.",
        rel: "Background on SNR loss from mismatch.",
      },
      {
        title: "Mismatched preamplifiers for decoupling non-adjacent elements",
        cite: "PMC8640609",
        url: "https://pmc.ncbi.nlm.nih.gov/articles/PMC8640609",
        status: "Not yet reviewed",
        key: "Mutual coupling between non-adjacent elements reduced by preamp mismatching.",
        rel: "Background on what preamp decoupling does.",
      },
    ],
  },
];

const FONT = "Calibri";
const para = (runs, opts = {}) => new Paragraph({ children: runs, spacing: { after: 60 }, ...opts });
const label = (l, t) => para([new TextRun({ text: l + ": ", bold: true }), new TextRun(t)]);

const children = [
  new Paragraph({ heading: HeadingLevel.TITLE, children: [new TextRun("References — Coil Element Fault Detection")] }),
  para([new TextRun({ text: "MRI_Viz_Project · last updated 2 November 2026", italics: true, color: "666666" })]),
  para([new TextRun("Papers, abstracts and patents relevant to detecting broken or degraded elements in multi-channel receive coils. Each entry notes how thoroughly it was reviewed, its key point, and why it matters for this project.")], { spacing: { after: 240 } }),
];

for (const s of SECTIONS) {
  children.push(new Paragraph({ heading: HeadingLevel.HEADING_1, children: [new TextRun(s.title)], spacing: { before: 300, after: 120 } }));
  for (const r of s.refs) {
    children.push(new Paragraph({ heading: HeadingLevel.HEADING_3, children: [new TextRun(r.title)], spacing: { before: 200, after: 60 } }));
    children.push(para([new TextRun({ text: r.cite, italics: true })]));
    children.push(para([new ExternalHyperlink({ link: r.url, children: [new TextRun({ text: r.url, style: "Hyperlink" })] })]));
    children.push(label("Reviewed", r.status));
    children.push(label("Key point", r.key));
    children.push(label("Relevance", r.rel));
    children.push(new Paragraph({ border: { bottom: { style: BorderStyle.SINGLE, size: 4, color: "DDDDDD", space: 4 } }, spacing: { after: 120 } }));
  }
}

const doc = new Document({
  styles: { default: { document: { run: { font: FONT, size: 22 } } } },
  sections: [{
    properties: { page: { size: { width: 12240, height: 15840 }, margin: { top: 1440, bottom: 1440, left: 1440, right: 1440 } } },
    children,
  }],
});

Packer.toBuffer(doc).then((buf) => { fs.mkdirSync(path.dirname(OUT), { recursive: true }); fs.writeFileSync(OUT, buf); console.log("wrote", OUT); });
