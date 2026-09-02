# Prior art: LLM reasoning for analog SPICE (survey, 2026-09-01)

Status: research notes only. Numbers below are taken from the cited paper
or from a live GitHub page fetched on this date. If a number is not in
those sources, it is in "Claims I could not verify".

No Spectre binary on this Windows machine. None of the academic systems
below were re-run here. Code-availability claims are "repo URL exists and
was fetched", not "I installed and reproduced the table".

## Bottom line

An LLM reasoning through a circuit does **not** replace a numeric
optimizer for device sizing today. It can replace many *wasted* optimizer
turns: the random starts, the wrong-region search, and the "I do not know
which knob to move" loops. It does not replace the last local refinement
that actually hits Gain / GBW / PM / power together.

The papers that meet multi-constraint specs keep a numeric method in the
loop (Bayesian optimization, TuRBO, CMA-ES, ABC, Optuna TPE, or a
gm/ID lookup table). AnalogCoder itself says it prioritizes functional
topology and skips parameter optimization because existing methods already
do that. AmpAgent's authors say the LLM is better at decomposing the
problem and proposing an initial point than at iterating on parameters.
ADO-LLM: an LLM agent alone missed at least one spec on both test
circuits; the hybrid met all specs. AnalogXpert: 23% success on 30 real
topology cases; plain GPT-4o got 3%.

Where the LLM helps most for a Virtuoso user: (1) review of a compact
netlist digest, not a raw ADE dump; (2) topology choice and "what is the
bottleneck"; (3) analytic first sizing (gm/ID, intrinsic gain, pole
guess); (4) interpreting a failed spec and picking the next experiment.
Where it does not: corners, Monte Carlo, mismatch, last-10% FoM, and
anything that needs a 5,000-line Spectre netlist in context.

The single most important correction to the thesis: **LLM reasoning
replaces the search for which experiment to run, not the optimizer that
runs it.**

---

## How to read the success rates

Papers do not measure the same thing. Treat every headline number as
scoped to its task:

- "Solved" / Pass@k on AnalogCoder's 24-task set means *functionally
  correct* PySpice that passes a scripted testbench (OP, DC sweep,
  function). It is not a sized, corner-clean op-amp.
- AnalogXpert success is *conditional topology* (structure requirements
  to subcircuit-level SPICE), not spec-driven sizing.
- LaMAGIC's 96% is *power-converter* voltage-ratio / efficiency on 3-5
  L/C/switch graphs, not CMOS analog.
- AnalogGenie "valid" is graph/sequence legality plus their FoM after a
  separate sizer. Novelty is "unseen topology", not "better than a human
  design".
- Atelier / AmpAgent / ADO-LLM / LEDRO report spec-meeting or FoM under
  a simulation budget. Those are the closest numbers to "does this save
  optimizer turns?"

No paper in this survey ingests a real ADE netlist of thousands of lines.
They work on 5-30 transistor textbook blocks, behavioral gm models, or
small PySpice decks.

---

## AnalogCoder (Lai et al., AAAI 2025)

- **Paper:** [arXiv:2405.14918](https://arxiv.org/abs/2405.14918),
  AAAI 2025, [doi:10.1609/aaai.v39i1.32016](https://doi.org/10.1609/aaai.v39i1.32016)
- **Code:** [github.com/laiyao1/AnalogCoder](https://github.com/laiyao1/AnalogCoder)
  (fetched; 194 stars on 2026-09-01)
- **Task:** Natural-language circuit request to executable PySpice
  (ngspice). Topology + a working bias, not industrial sizing.
- **Approach:** Training-free agent. Domain prompt + CoT plan +
  in-context two-stage amp example. Four-stage feedback: requirement
  check, simulation / OP check, DC sweep, function check. Successful
  basic blocks go into a circuit tool library and are retrieved for
  composite tasks. They generate Python because LLMs have more Python
  training data than SPICE.
- **Benchmark:** 24 circuits (easy / medium / hard by device count).
  Pass@k with n=15 or 30 trials. Default 3 repair attempts (2 for
  composites); they measured that success falls off after attempt 3.
- **Measured result:** Paper: AnalogCoder solved **20 / 24**, GPT-4o
  without the agent **15 / 24**. GitHub leaderboard (same repo, may be
  a later table): AnalogCoder+GPT-4o Pass@1 **66.1**, Pass@5 **75.9**,
  20 solved; AnalogCoder+Claude-3.5 Pass@1 **76.1**, Pass@5 **86.3**,
  22 solved; GPT-4o without tools Pass@1 **54.2**, 15 solved. Fine-tuned
  GPT-3.5 reduced syntax errors but did not unlock new circuit types.
- **Honest limit:** Authors write that they "prioritize the correct
  functionality of analog circuits, without extensive parameter
  optimization, already well-addressed by existing advanced
  methodologies". Failed designs can be a single wrong wire. AnalogGenie
  later scores AnalogCoder at **57.3% valid** and FoM **1.7** on their
  own validity/FoM protocol (not the 24-task Pass@k).
- **Code availability:** Public. PySpice 1.5 / Python 3.10. Needs an
  LLM API key.

## AnalogCoder-Pro (Lai et al., TCAD 2026)

- **Paper:** [arXiv:2508.02518](https://arxiv.org/abs/2508.02518),
  IEEE TCAD 2026, [doi:10.1109/TCAD.2026.3673493](https://doi.org/10.1109/TCAD.2026.3673493)
- **Code:** [github.com/laiyao1/AnalogCoderPro](https://github.com/laiyao1/AnalogCoderPro)
  (fetched; 65 stars)
- **Task:** Unify topology generation and device sizing. 13 circuit
  types, 28 circuits designed in the paper's claim.
- **Approach:** Same PySpice + tool-library loop, plus multimodal
  repair (simulator logs + Matplotlib waveforms captioned by a
  multimodal LLM). After a legal netlist: LLM extracts knobs, then
  **Bayesian optimization** (Optuna TPE, **1000 trials**, 25% random
  init). Multi-resolution DC search finds Vin so Vout ~ VDD/2 before
  the main loop. Target-guided prompts ("optimize FoM = GBW*CL/Power")
  steer topology choice.
- **Measured result:** Stronger backbones raise generation success
  (GPT-5 > GPT-4.1 in their Table IV). Ablation: drop feedback, CoT,
  or the tool library and Pass@k falls. Waveform captions help. For
  each first-success Claude-3.5 topology they compare FoM with and
  without BO; BO is what produces the large FoM jumps (example in
  text: Gain 49.3 dB, BW 0.34 MHz, Power 0.13 mW, CL 100 pF, FoM
  73.9 after sizing). They claim up to ~2x op-amp FoM vs AnalogGenie
  on their protocol.
- **Honest limit:** Sizing is not LLM arithmetic. It is 1000 BO
  evaluations on a PySpice deck. Still not an ADE netlist.
- **Code availability:** Public.

## AnalogGenie (Gao et al., ICLR 2025 Spotlight)

- **Paper:** [arXiv:2503.00205](https://arxiv.org/abs/2503.00205),
  ICLR 2025, [OpenReview](https://openreview.net/forum?id=jCPak79Kev)
- **Code:** [github.com/xz-group/AnalogGenie](https://github.com/xz-group/AnalogGenie)
  (fetched; 113 stars)
- **Task:** Discover analog *topologies* as sequences, not size them
  in-loop. Decoder-only transformer predicts the next device pin.
- **Approach:** Pin-level undirected graph serialized as an Eulerian
  circuit (avoids device-node ambiguity: which pin does the edge hit?).
  Dataset: **>3000** distinct topologies from six textbooks plus IEEE
  papers (Op-Amp, LDO, bandgap, comparator, PLL, LNA, PA, mixer, VCO),
  then ~70x Eulerian / BFS augmentation (~227k sequences). Pretrain
  then finetune on a family. Sizing / FoM is a separate optimizer
  after generation.
- **Measured result (their Table 1):**

  | System | Valid % | Max devices | Novel % | Op-amp FoM | Converter FoM | Bandgap FoM |
  |---|---:|---:|---:|---:|---:|---:|
  | CktGNN | 67.5 | 22 | 93.1 | 10.9 | - | - |
  | LaMAGIC | 68.2 | 4 | 12.7 | - | 2.2 | - |
  | AnalogCoder | 57.3 | 10 | 8.9 | 1.7 | - | - |
  | AnalogGenie pretrained | 73.5 | 63 | 98.9 | 19.3 | 2.5 | 17.2 |
  | AnalogGenie + finetune | **93.2** | 56 | 99 | **36.5** | 3.3 | 21.9 |

  Unaided (no augmentation) overfit and produced essentially no valid
  circuits. AnalogCoder novelty 8.9% is expected: it is a task-completion
  agent, not a topology explorer.
- **Honest limit:** This is a trained generative model, not "an LLM
  reasons about your netlist". FoM comparisons across papers are not
  apples-to-apples (different sizers, PDKs, FoM definitions). A novel
  valid graph is not a tape-out.
- **Code availability:** Public.

## LaMAGIC / LaMAGIC2 (Chang et al., 2024 / 2025)

- **LaMAGIC:** [arXiv:2407.18269](https://arxiv.org/abs/2407.18269)
- **LaMAGIC2:** [arXiv:2506.10235](https://arxiv.org/abs/2506.10235),
  PMLR 267 (ICML 2025)
- **Code:** [github.com/turtleben/LaMAGIC](https://github.com/turtleben/LaMAGIC)
  (fetched; 14 stars)
- **Task:** One-shot topology generation for *custom power converters*
  (C, L, two-phase switches, VIN/VOUT/GND). Specs: voltage conversion
  ratio and efficiency. Not a CMOS OTA sizer.
- **Approach:** Supervised finetune of a language model on graph
  formulations (canonical sequences, adjacency-matrix / T5-style
  masks, float vs character specs). Dataset: random non-isomorphic
  3/4/5-component topologies x duty cycles {0.1, 0.3, 0.5, 0.7, 0.9},
  simulated in **ngspice**, invalids dropped. ~120k train / 12k eval;
  later 6-component transfer set. Atelier notes the training corpus
  needed **>100,000 circuit simulations**.
- **Measured result:** LaMAGIC: success **up to 0.96** under tolerance
  **0.01** (ratio / efficiency). LaMAGIC2 SFCI formulation: **+34%**
  success at the same 0.01 tolerance, **10x lower MSE**, up to
  **+58.5%** transfer to graphs with more vertices. AnalogGenie scores
  LaMAGIC at 68.2% valid and 4-device scale on *their* protocol.
- **Honest limit:** The 96% number is the most mis-cited result in
  this literature. It is not analog CMOS sizing. AaLLM and AnalogGenie
  both note LaMAGIC does not size transistors.
- **Code availability:** Public (small academic repo).

## ADO-LLM (Yin, Wang, Xu, Li, ICCAD 2024)

- **Paper:** [arXiv:2406.18770](https://arxiv.org/abs/2406.18770),
  ICCAD 2024, [doi:10.1145/3676536.3676816](https://doi.org/10.1145/3676536.3676816)
- **Code:** No official repo found. Author page
  [reminiscenty.github.io](https://reminiscenty.github.io/) links the
  paper only.
- **Task:** Transistor sizing only (topology given). Two circuits in
  a **commercial 90 nm** PDK, **HSPICE**.
- **Approach:** GPT-3.5 Turbo agent + GP-BO (RBF, expected
  improvement). LLM reads an annotated netlist, explains each device,
  proposes a point. Sampler feeds the top-k FoM points as in-context
  demos. BO explores; LLM exploits high-value regions. Zero-shot LLM
  init vs random init is an ablation.
- **Measured result (Tables 3, 5, 6, 7):**

  Two-stage amp (14 free params; Gain>=60 dB, CMRR>=75, GBW>=1 MHz,
  PM>=60, Power<=30 uW):

  | Method | Budget | FOM | Missed specs |
  |---|---|---:|---:|
  | GP-BO | 5+5x20 | 1.89 | 1 (gain 27 dB) |
  | GP-BO | 5+5x80 | 2.10 | 1 (power 66 uW) |
  | LLM agent | 5+1x100 | 0.20 | 1 (power 39 uW) |
  | **ADO-LLM** | **5+5x20** | **3.52** | **0** |

  Hysteresis comparator (12 params): GP-BO 20-iter FOM -3.38, 2
  misses; LLM agent FOM -1.35, 1 miss; ADO-LLM FOM **0.90**, **0**
  misses. LLM init alone improved GP-BO but still left misses. LLM
  with no ICL missed **3** amp specs (FOM -1.78).
- **Honest limit:** Two circuits. Closed-source GPT-3.5. Authors say
  GP will not scale to large design spaces and the LLM is not
  analog-specialized. This is the cleanest published evidence that
  **LLM-alone sizing fails multi-constraint trade-offs** and that
  the hybrid is the thing that works.
- **Code availability:** Not public (as of this survey).

## AnalogXpert / Atelier (two different papers)

These are **not** the same system. AnalogXpert is topology synthesis
as subcircuit SPICE. Atelier is a multi-agent GoT that selects /
modifies behavioral op-amp topologies and sizes with CMA-ES.

### AnalogXpert (Zhang et al., ISEDA 2025)

- **Paper:** [arXiv:2412.19824](https://arxiv.org/abs/2412.19824),
  ISEDA 2025
- **Code:** Flow lives under
  [github.com/PKU-IDEA/PANDA](https://github.com/PKU-IDEA/PANDA)
  (`analogxpert`, OpenAI-compatible topology calls). PANDA itself is
  a later intent-to-layout stack (Spectre, DE sizing, Virtuoso
  bridge). Treat AnalogXpert-the-paper and PANDA-the-repo as related
  but not identical.
- **Task:** Conditional topology: structure requirements (stages,
  input type, feedback) to **subcircuit-level SPICE**, not ideal gm
  sources.
- **Approach:** Extensible subcircuit library (one-path / two-path
  blocks, R, C). CoT: pick blocks, then wire them. Rule-based
  proofreading (annotate I/O, I/I, V terminals; reject floating
  current ports, wrong polarity stages, stage-order bugs). Up to 10
  proofreading rounds.
- **Benchmark:** 2k synthetic + **30 real** cases.
- **Measured result (Table II, GPT-4o backbone):** AnalogXpert
  **801/2000 = 40%** synthetic, **7/30 = 23%** real. GPT-4o
  **3%/3%**. AnalogCoder adapted to this task **8%/6%**. GPT-3.5 +
  their representation but no full agent: 28% synthetic, **0/30**
  real. Proofreading rounds raise the rate (1 / 5 / 10).
- **Honest limit:** 23% on 30 real cases is the most industrial-shaped
  topology number in this survey, and it is still a fail-often
  result. Failures they show: floating current terminals, P- vs
  N-cascode, second-stage input tied to first-stage input.
- **Code availability:** Partial, via PANDA.

### Atelier (TechRxiv 2024)

- **Paper:** [TechRxiv 172668168.88938111/v2](https://doi.org/10.36227/techrxiv.172668168.88938111/v2)
- **Code:** No public repo found.
- **Task:** Spec-driven op-amps (SMIC 180 nm, Spectre) and a StrongArm
  comparator (SMIC 40 nm). Behavioral VCCS stages + compensation
  paths, then transistor mapping.
- **Approach:** Compact knowledge base (~60,124 tokens from two
  review papers + eight structure papers + netlist manuals;
  **human-reviewed**; they say one topology-modification entry was
  wrong and was hand-fixed). Five GLM-4 agents in a graph-of-thoughts:
  analysis, topology select, modify, **CMA-ES sizer** (pycma, 100
  sims per topology), decider with backtracking. RAG over the
  curated base, not raw PDFs.
- **Measured result:** GPT-4 and GLM-4 *without* the knowledge base
  **failed every spec set** (wrong NMC wiring, syntax-broken
  netlists, trial-and-error sizing, vague mods like "adjust the
  feedback network"). Atelier-3 (up to 3 topology edits, 400-sim
  budget) is the only method they report as succeeding in **all
  runs** across S-1..S-5, including specs where BO/RL baselines
  fail. Vs those black-box methods: **1.57-5.33x** FoM,
  **28-87x** runtime. Vs Artisan: **1.19-3.44x** FoM, **1.37-4.65x**
  runtime. Comparator: GPT-4/GLM-4 produced no simulating netlist;
  Atelier succeeded in all runs.
- **Honest limit:** Behavioral first, then a mapping step. Knowledge
  base is hand-gated (that is a feature, not a bug). No public code.
  Exact per-spec success percentages in Table IV were not recoverable
  as clean integers from the HTML extract; the qualitative claims
  above are from the prose that accompanies that table.
- **Code availability:** Not found.

## LADAC (Liu, Liu, Du, Du, TechRxiv 2024)

- **Paper:** [TechRxiv 170473941.10097233/v1](https://doi.org/10.36227/techrxiv.170473941.10097233/v1)
  (8 Jan 2024). Often cited as the first LLM *agent* for analog
  design (ADO-LLM, AnalogXpert both cite it that way).
- **Code:** No public repo found.
- **Task:** Size a given topology via GPT-4 + a local knowledge
  library + simulator I/O (Cadence MDL to read metrics) + Artificial
  Bee Colony.
- **Approach:** Agent decides the next design move from specs and
  retrieved notes. Interactive tools size devices and return
  measurements.
- **Measured result:** **Three** circuits, no large benchmark: a
  2-stage amp and a 3-stage NMCNR, both with open-loop gain
  **>80 dB**, and a ring oscillator at **100 MHz**. Their own GPT-4
  chat probes failed: a 3rd-order RC LPF netlist was the wrong
  network; a 5T OTA netlist had multiple structural errors. They
  state outright that iterative sizing "cannot be readily generated
  by LLMs in a single attempt".
- **Honest limit:** Existence proof, not a success-rate paper. The
  interesting content is the failure of conversational GPT-4 on
  textbook netlists.
- **Code availability:** Not found.

## Artisan (Chen et al., DAC 2024)

- **Paper:** DAC 2024,
  [doi:10.1145/3649329.3655903](https://doi.org/10.1145/3649329.3655903)
  (ACM page fetched; PDF is paywalled)
- **Code:** Paper says "will be released". No official Artisan repo
  found. Same group later published
  [github.com/zhchenfdu/whiteop](https://github.com/zhchenfdu/whiteop)
  (behavioral HSPICE + optional gm/ID map via Spectre, adapted from
  [jialinlu/OPAMP-Generator](https://github.com/jialinlu/OPAMP-Generator)).
  That is **not** verified as the DAC artifact.
- **Task:** Automated op-amp design with a domain LLM.
- **Approach (from abstract + later citations):** Bidirectional
  topology / language representation. ToT + CoT as a multi-agent
  Q&A. Llama2-7B finetuned on a **190 million token** op-amp corpus
  (Atelier's comparison). Sizing via **gm/ID scripts**, not a
  black-box LLM guess. AnalogCoder-Pro describes Artisan as
  **behavioral-level** and "omitting device-level refinement".
- **Measured result (abstract only):** Beats "SOTA optimization-based
  methods and benchmark LLMs" on success rate, metrics, and
  interpretability; up to **50.1x** faster. Atelier's later bake-off
  says Artisan is slower and less robust than Atelier, fails some
  runs (wrong netlists, mixed-up design equations, one-shot topology
  edit, no backtrack).
- **Honest limit:** I do not have the DAC PDF tables. Do not quote a
  success percentage for Artisan. The 50.1x figure is wall-clock vs
  their optimizer baseline, not "50x better circuits".
- **Code availability:** Official release unverified. whiteop is
  related research code.

## AmpAgent (Liu et al., 2024) and other RAG assistants

### AmpAgent

- **Paper:** [arXiv:2409.14739](https://arxiv.org/abs/2409.14739)
- **Code:** No public repo found. LangChain + GPT-4-1106-preview +
  **Cadence Spectre** on Linux.
- **Task:** Port seven multi-stage compensation families (SMC,
  NMCNR, NGCC, DFCFC, TCFC, IAC, AZC) from literature to a new
  process / spec.
- **Approach:** Three ReAct agents. Literature Analysis: Mathpix PDF
  to Markdown/LaTeX, embed, RAG, plus prompts so formulas are not
  dropped. Mathematics Reasoning: derive gm / stability sub-problems.
  Device Sizing: simulator + **ABC or TuRBO** on those sub-problems,
  then an optional global polish. **Schematic-to-netlist is still
  manual**; GPT-4o failed their diagram-to-netlist test.
- **Measured result:** vs original papers, IFOM_S improved
  **1.63-27.25x** (NGCC 36 -> 981 is the 27.25x). vs ABC / TuRBO
  from a cold start (max 100 iters):

  | Amp | AmpAgent success | ABC success | TuRBO-5 success |
  |---|---:|---:|---:|
  | SMC | 100% (16 it, 245 s) | 68% | 95% |
  | NMCNR | 100% (19 it, 444 s) | 46% | 84% |
  | NGCC | 98% | 46% | 95% |
  | DFCFC | 95% | 14% | 83% |
  | TCFC | 96% | 54% | 89% |
  | IAC | 97% | failed @100 | 73% |
  | AZC | 96% | failed | failed |

  Without the "further optimization" pass, several amps miss PM
  (NMCNR 32.5 deg, DFCFC 1.2 deg). Authors: AmpAgent is "better
  suited for decomposing sub-optimization problems and providing
  initial solutions ... rather than directly performing iterative
  optimization on parameters itself." Failures they saw were GPT-4
  action hallucinations.
- **Code availability:** Not found.

### Other RAG / memory systems (shorter)

- **LEDRO** ([arXiv:2411.12930](https://arxiv.org/abs/2411.12930),
  [github.com/dimplekochar/LEDRO](https://github.com/dimplekochar/LEDRO),
  15 stars): Llama3-70B proposes a *reduced search box*, TuRBO
  sizes inside it, 10 rounds, calibration-point ICL so they do not
  retrain per topology/node. **88 circuits**: 22 op-amp topologies x
  4 FinFET nodes. vs best baseline: **+13% FoM and 2.15x** speed on
  low-complexity, **+48% FoM and 1.7x** on high-complexity. This is
  the cleanest "LLM shrinks the sweep" paper.
- **EEsizer** ([arXiv:2509.25510](https://arxiv.org/abs/2509.25510),
  also [arXiv:2504.11497](https://arxiv.org/abs/2504.11497);
  [github.com/eelab-dev/EEsizer](https://github.com/eelab-dev/EEsizer),
  36 stars): LLM *is* the sizer (function calls into ngspice, CoT,
  5% spec tolerance, no BO). o3 and Claude 3.5 Sonnet: **100%**
  success on their 6 small circuits within 20 iters; GPT-4.1
  **40%** on the ring oscillator; Haiku failed oscillator and 5T
  OTA. On a 20-transistor op-amp across PTM 180/130/90 nm, success
  **drops as Lmin shrinks**. 50-sample variation: gain pass **78%**,
  UGBW **76%**; they say Monte Carlo still needs to be in the loop.
- **AnalogSAGE** ([arXiv:2512.22435](https://arxiv.org/abs/2512.22435),
  [github.com/xz-group/AnalogSAGE](https://github.com/xz-group/AnalogSAGE),
  15 stars): three-stage agents + four memory layers + **BO +
  ngspice + SKY130**. 10 spec-driven op-amp tasks. Headline:
  **10x** pass rate, **48x** Pass@1, **4x** smaller search box vs
  *their* baselines. Those multipliers are relative, not 100%
  absolute. Uses AnalogGym-style scripts.
- **AnalogAgent** ([arXiv:2603.23910](https://arxiv.org/abs/2603.23910)):
  training-free MAS + self-evolving memory playbook. On a 30-task
  AnalogCoder-style set: GPT-5 **97.4 / 100.0** Pass@1/5; Gemini
  2.5 Flash **92.0 / 99.9**. Hard-task Pass@1 **89.8** full vs
  **43.7** with both modules off. AnalogCoder-Pro on the same
  protocol: **88.6 / 98.2**. Time-to-first-success ~1.3 min vs 2.1
  (Pro) / 2.6 (SPICEPilot). No official repo confirmed in this
  survey.
- **Masala-CHAI** ([arXiv:2411.14299](https://arxiv.org/abs/2411.14299),
  [masala-chai-llm.github.io](https://masala-chai-llm.github.io/)):
  schematic-image to SPICE dataset (~7,500 netlists). Fine-tunes
  used inside AnalogCoder: **+46% Pass@1**. This is data, not an
  agent.
- **SPICEPilot** ([arXiv:2410.20553](https://arxiv.org/abs/2410.20553)):
  Pilot Prompt + PySpice + human/script repair of keyword bugs.
  AnalogAgent later reports Pass@1 **50.1**, Pass@5 **96.5** (works
  if you sample enough). No official repo confirmed here.
- **AnalogTester** ([arXiv:2507.09965](https://arxiv.org/abs/2507.09965)):
  LLM pipeline for analog *testbenches* (op-amp, BGR, LDO) in TED,
  not sizing. Useful as a pattern: generate the measurement deck
  separately from the DUT.
- **AaLLM** ([arXiv:2608.13472](https://arxiv.org/abs/2608.13472)):
  claims end-to-end topology + sizing with RAG and a Designer /
  Critic / Evaluator triad, **3-4.5x fewer SPICE calls** vs
  multi-agent SOTA, **40x** wall-clock vs their optimizer baseline,
  novel topologies up to 3x FoM. Very new; treat as unverified
  beyond the abstract until reproduced.
- **Self-calibrating analytic sizer**
  ([arXiv:2604.07387](https://arxiv.org/abs/2604.07387)): LLM
  writes a Python sizing function; one DC OP calibrates process
  params; 2-7 sims to converge on 8-30 transistor amps across 40/90/180
  nm. Strong "equations + one OP, not 1000 BO steps" claim. No
  public code confirmed here.
- **AnalogMaster** ([arXiv:2604.20916](https://arxiv.org/abs/2604.20916)):
  image-to-layout agent; GPT-5 best; smaller VLM backbones confuse
  NMOS/PMOS and invent wires. Relevant as a warning for schematic
  photos.

---

## CktGNN (non-LLM baseline, cited by everyone)

- **Paper:** [arXiv:2308.16406](https://arxiv.org/abs/2308.16406),
  ICLR 2023
- **Code:** [github.com/zehao-dong/CktGNN](https://github.com/zehao-dong/CktGNN)
- **Task:** VAE over DAG / subgraph-basis encodings; joint topology +
  device features. Open Circuit Benchmark: **10k** op-amps.
- **Why it matters:** AnalogGenie's validity/FoM table is partly a
  comparison against this, not against a human designer. It is the
  graph-ML ancestor, not an LLM.

---

## Honest LLM weaknesses (with cited rates)

1. **Hallucinated topology / netlist.** GPT-4 failed LADAC's RC LPF
   and 5T OTA chats. GPT-4o failed AmpAgent's schematic-to-netlist
   test. Atelier: GPT-4 put the NMC cap on the wrong nodes; GLM-4
   dropped a cap. AnalogXpert: GPT-4o **3%** on real and synthetic
   structure tasks. AnalogGenie: AnalogCoder **57.3%** valid on their
   generator protocol. AnalogCoder: one wrong wire kills the circuit;
   after 3 repair attempts further tries rarely help.
2. **Cannot do the numeric optimization an optimizer does well.**
   AnalogCoder declines to try. Atelier: "LLMs lack the precise
   numerical capabilities needed for accurate parameter tuning" and
   they call CMA-ES. AmpAgent: use the LLM for decomposition and
   init, not for the parameter loop. ADO-LLM LLM-only: missed power
   or gain on both circuits. EEsizer is the counter-example on
   *small* circuits, and even there 90 nm and MC expose the gap.
3. **Multi-constraint trade-offs.** ADO-LLM Table 3 is the exhibit:
   GP-BO after 4x budget still misses power; the LLM agent blows
   GBW (93 MHz) and misses power. Only the hybrid hit all five
   specs. Atelier black-box BO/RL "low success rates" on the hard
   spec sets (S-2 gain, S-4 power, S-5 1000 pF). AmpAgent without
   the polish pass misses PM on NMCNR and DFCFC.
4. **Context limits on large netlists.** Every system above either
   (a) emits short PySpice, (b) talks in subcircuit blocks, (c)
   uses a 60k-token curated KB, or (d) keeps a compressed memory /
   playbook. None of them put a 5,000-line ADE `input.scs` in the
   prompt. This skill's own digest path (outline / summary /
   device_rollup) is the correct reaction: ~270x token cut on a
   5k-line netlist (83,825 -> 309). AnalogSAGE's "stratified memory"
   and AnalogAgent's playbook are the same idea.
5. **Process / PDK transfer.** LEDRO needs calibration-point
   synthesis to move across FinFET nodes. EEsizer success falls
   180 -> 90 nm. Self-calibrating paper claims one OP extracts
   uCox / lambda / Vth; that is a 2026 preprint, not a Virtuoso
   PDK flow.
6. **Hallucinated equations.** Atelier vs Artisan: mixed-up design
   formulas, "add Miller" to an NMC that already has it. AmpAgent
   RAG still lost formulas until they wrote retrieval prompts;
   schematics never came out of RAG.
7. **Over-claim risk on "96%" and "97%".** LaMAGIC 96% is converters.
   AnalogAgent 97.4% is Pass@1 on the AnalogCoder *functional*
   benchmark with GPT-5, not Spectre sign-off.

---

## Classical methods the LLM would be replacing

A fair comparison is not "LLM vs a drunk Monte Carlo". It is LLM vs
the tools analog designers already trust.

### gm/ID (Jespers 2009; Jespers & Murmann 2017)

- Jespers, *The gm/ID Methodology*, Springer 2009,
  [doi:10.1007/978-0-387-47101-3](https://doi.org/10.1007/978-0-387-47101-3)
- Jespers & Murmann, *Systematic Design of Analog CMOS Circuits*,
  CUP 2017, [doi:10.1017/9781108125840](https://doi.org/10.1017/9781108125840);
  tables/scripts: [github.com/bmurmann/Book-on-gm-ID-design](https://github.com/bmurmann/Book-on-gm-ID-design)

Pick inversion level (gm/ID), read W from ID / (gm/ID * gm_needed),
L from gain / matching / flicker. This is still the right *first*
size. Artisan, whiteop, and
[jiyuanduan001-oss/LLM_Amplifier_Sizing](https://github.com/jiyuanduan001-oss/LLM_Amplifier_Sizing)
all bolt an LLM in front of a LUT, not instead of it. This skill
already prints gm/ID from an OP (`op.table()`, `d.gm_id`).

**When it wins:** first-cut W/L, inversion-level intent, teaching,
cross-node porting if you rebuild the LUT. **When it loses:**
unmodeled poles, layout parasitics, specs that are not in the LUT
axes.

### Geometric programming (Hershenson, Boyd, Lee 2001)

- [IEEE TCAD 20(1):1-21](https://doi.org/10.1109/43.905671),
  [PDF](https://web.stanford.edu/~boyd/papers/pdf/opamp.pdf)
- GPCAD: [ICCAD tool paper](https://web.stanford.edu/~boyd/papers/pdf/gpcad_iccad.pdf)

If gain, bandwidth, power, area can be written as posynomials, GP
finds the **global** optimum in seconds and tells you when the spec
set is infeasible. That is a stronger guarantee than any LLM or BO
run.

**When it wins:** fixed topology, long-channel / posynomial-friendly
models, trade-off curves, "is this spec set impossible?". **When it
loses:** short-channel BSIM4 that is not posynomial, topologies you
have not modeled, anything that needs a real Spectre corner.

### Bayesian optimization / TuRBO / CMA-ES / DE / ABC

This is what the hybrid papers actually call.

- GP-BO: ADO-LLM, AnalogCoder-Pro (Optuna TPE, 1000 trials),
  AnalogSAGE.
- TuRBO: LEDRO, AmpAgent baseline
  ([uber-research/TuRBO](https://github.com/uber-research/TuRBO)).
- CMA-ES: Atelier (pycma).
- Differential evolution: PANDA sizing; this skill's `tune()` uses
  bisection or `scipy.least_squares`.
- ABC: AmpAgent / LADAC.
- RL: AutoCkt ([github.com/ksettaluri6/AutoCkt](https://github.com/ksettaluri6/AutoCkt)),
  GCN-RL; LEDRO beats an RL baseline on transfer across nodes
  because RL retrains and LEDRO does not.

**When a numeric optimizer is simply the right tool:** topology is
chosen; knobs are continuous W/L/R/C; constraints are numbers; you
can afford 50-1000 Spectre calls; you want a Pareto front. An LLM
proposing random W/L in that regime is a worse, more expensive BO
with extra hallucination.

### Corners + Monte Carlo + sensitivity

Sign-off, not design. Spectre `montecarlo`, ADE XL, mismatch,
PVT. This skill's translator **does not** fake `montecarlo` /
`stb` / `pss`; it lists them in `tr.unsupported`. EEsizer's 78% /
76% variation pass rates are why. An LLM cannot replace a 200-point
MC. It can read a histogram and say "offset is the yield killer,
widen the pair", then you re-run MC.

Sensitivity / xf / pz: once you have poles and `dF/dW`, a one-line
gradient beats a sweep. That is classical, not LLM.

---

## Where the LLM genuinely beats a sweep

- **Naming the bottleneck.** "M3 is in triode", "the mirror pole
  is the one you moved", "you asked for 80 dB and a 5T OTA cannot
  do that". A sweep will burn 200 points discovering the same
  fact. AnalogCoder's OP / DC / function checks and this skill's
  `op.check_saturation()` are the cheap version.
- **Topology choice and local surgery.** Atelier's modifier
  (series RC on a compensation path, then backtrack when NCV
  got worse). AmpAgent picking NMCNR vs DFCFC from a paper.
  AnalogXpert proofreading a wrong cascode polarity. A sweep
  does not change topology.
- **Shrinking the box.** LEDRO: LLM outputs a region, TuRBO
  works inside it. AnalogSAGE: 4x smaller search box. ADO-LLM
  init: LLM seeds beat random and almost match a 4x-longer BO.
- **Literature / equation porting.** AmpAgent RAG + math agent
  turns a PDF transfer function into gm targets, then ABC
  finishes. That is hours of hand algebra, not a sweep.
- **Writing the deck / testbench.** AnalogCoder / SPICEPilot /
  AnalogTester. Generating PySpice or a TED doc from a prompt
  is a real time save if a checker runs it.
- **Review, not design.** Compact digest + OP table + "what
  happens if I double this W" as a *prediction*, then one
  confirm sim. That is the user's (a) direction and it is the
  part the evidence supports.

## Where a sweep still wins

- **Last local optimum on a fixed topology.** AnalogCoder-Pro's
  1000 TPE trials, Atelier's 100 CMA-ES evals per topology,
  ADO-LLM's GP-BO batch. The LLM does not do this well.
- **Tight coupled specs.** Gain vs power vs PM vs GBW vs offset.
  LLM-alone in ADO-LLM and Atelier fails here.
- **High-dimensional W/L after ~15 free variables.** Authors of
  ADO-LLM say so. EEsizer's 20-transistor op-amp already droops
  at 90 nm.
- **PVT, mismatch, yield.** MC / corners. No LLM paper replaces
  this. EEsizer tells you to add it.
- **Posynomial-friendly closed form.** GP (Boyd) is faster and
  globally optimal. Do not BO a problem you can GP.
- **Anything that needs Spectre analyses ngspice cannot do.**
  `stb`, `pss`, `xf`, `pz`, `sp`, `montecarlo` (this skill
  already refuses to fake them).
- **A 5,000-line ADE netlist as the object of generation.**
  Nobody has a published success rate on that. If you dump it
  into a chat, you lose.

---

## The loop worth building

Given: analog IC work in Cadence Virtuoso on Linux; this Windows
box has Python, ngspice, and this skill, and **no Spectre**. Netlists
must travel. Sibling notes cover Python-to-CDL / spiceIn; this
section is the *reasoning* loop those files should serve.

### Do not build

- An AnalogCoder clone that emits PySpice for a foundry PDK and
  calls it a design.
- A "the LLM sizes W/L" loop without a numeric refiner (ADO-LLM
  and Atelier already ran that ablation; it loses).
- Fine-tuning Llama on 190M tokens (Artisan). You do not have the
  corpus, and Atelier beat that approach with 60k curated tokens
  plus CMA-ES.
- Feeding raw ADE `input.scs` to the model.

### Do build (concrete)

```
Linux / Virtuoso                         Windows / this skill
----------------                         -------------------
ADE "Save netlist"  ------------------>  SN.parse_file
spiceOut / CDL      ------------------>  ND.outline / summary / rollup
                                         op = operating_point; op.table()
                                         op.check_saturation()
                                         LLM REVIEW on the digest only
                                         (bottleneck, region, next knob)
                                         optional: TR.translate + simulate
                                         optional: tune(knobs, targets)
                                         LLM interprets miss vs hit
export configured   <------------------  SN.set_param / write_netlist
  .scs / CDL                             (byte-identical round-trip)
Spectre corners/MC  (sign-off only)
```

**Review path (user goal a).** Digest, do not dump. Escalate with
`show_subckt`, `net_report`, `grep`. Every claim about gain or
bandwidth is produced twice: hand from extracted gm/ro, and one
sim, then `Reconcile`. The LLM's job is "M4 is the gain bottleneck,
raise L or cascode", not a 200-point W sweep.

**Design path (user goal b).**

1. Human or LLM picks a *named* topology (5T, mirrored load,
   two-stage Miller, folded cascode). Do not invent graphs.
   AnalogXpert's 23% real-case rate is why.
2. Analytic first size: gm/ID LUT or `op.table()` on a PTM
   stand-in. State inversion level, current, W, L in a table.
3. `tune()` or a small DE/BO (tens of sims, not 1000) on the
   translated deck, knobs = the 4-8 devices the review named.
4. LLM reads the miss list and either (i) moves one knob's
   bounds, (ii) changes topology with a one-sentence reason,
   or (iii) stops and says the spec set is infeasible.
5. Export Spectre/CDL + a sidecar of CDF / PDK names (sibling
   netlist-generation note). On Linux: `spiceIn` or ADE load,
   then **Spectre** corners/MC. Never treat ngspice/PTM as
   sign-off.

**Context budget.** Default views: `outline` (~10 lines),
`summary` (~50), `device_rollup`. Pull raw lines only for the
net you are arguing about. Keep a playbook of "what failed last
time" (AnalogAgent / AnalogSAGE), not the last 20 waveforms.

**Anti-hallucination (reuse these; they are already in the papers
and in this skill).**

1. Parse, do not regex, the netlist (`spectre_netlist`).
2. Diff the digest after every edit (`ND.diff_digest`).
3. Saturation / region check before any AC claim.
4. Scripted function checks (AnalogCoder's four stages).
5. Refuse unsupported analyses instead of aliasing them.
6. Human-gate any knowledge card you RAG (Atelier's one bad
   entry).
7. Numeric refiner owns W/L; LLM owns the hypothesis.

That loop is LEDRO + AmpAgent + AnalogCoder's checker + this
skill's digest/tune, not "GPT writes a sized folded cascode".

---

## Practical tooling (how they actually call SPICE)

| System | Simulator | How they call it | How they parse | How they keep context small | How they catch hallucinations |
|---|---|---|---|---|---|
| AnalogCoder / Pro | ngspice via **PySpice** | generated Python `Circuit` + `.simulator()` | OP, DC, function scripts in `problem_check/` | Python, not SPICE; tool library of subcircuits; 3 repair rounds | 4-stage checkers; Pro adds waveform captions |
| LaMAGIC | ngspice | batch sim while building the dataset | voltage ratio, efficiency | SFT so inference is one pass; no ADE | drop topologies the simulator rejects |
| AnalogGenie | (post-hoc sizer; paper uses FoM after generation) | not an in-loop Spectre agent | validity of Eulerian sequence + FoM | pin tokens, not netlist text | pin-level graph removes ambiguous edges |
| ADO-LLM | **HSPICE** | evaluate each proposed x | metric extract -> FoM | annotated short netlist + top-5 demos | format parser; resimulate every point |
| AmpAgent / Atelier | **Cadence Spectre** | LangChain / agent writes size, reads MDL / logs | Gain, GBW, PM, power | RAG over one paper or a 60k-token KB | Spectre is ground truth; Atelier human-reviews KB |
| LEDRO | AutoCkt-style sim wrapper | TuRBO inside LLM box | FoM of gain/UGB/PM/current | LLM outputs bounds, not a netlist | every point is simulated |
| EEsizer | ngspice | LLM **function calling** | 5% tolerance on listed metrics | history of last attempts only | variation test; they still want MC |
| AnalogSAGE | ngspice + **SKY130** | AnalogGym-like scripts + BO | spec pass/fail | 4 memory layers, 3 reasoning iters | sim-grounded; open SKY130 |
| This skill | ngspice (PTM); Spectre **not** installed | `translate` / `simulate` / `tune` | `operating_point`, `bode_summary`, `Reconcile` | `ND.outline` / `summary` / rollup | saturation check; `tr.unsupported`; digest diff |

Reusable pieces for the Windows/Linux split:

- Generate or edit on Windows as **structured IR** (this skill)
  or Python-as-code (AnalogCoder style) or CDL+JSON placement
  (cdl_gen; see sibling netlist-generation note). Do not make
  the LLM emit foundry Spectre by hand.
- Simulate cheaply here (ngspice/PTM or Sky130) for topology /
  first-cut. Sign off on Linux Spectre.
- Parse results as **tables** (device gm/ID/region, bode
  summary, miss list), never raw log paste.
- Keep a checker that is not the LLM: saturation, dangling
  nets, unit lint (`M` mega vs milli), missing `include`
  section (see `references/ade-netlist-errors.md`).

---

## Claims I could not verify

- **Artisan DAC tables.** ACM page and abstract only. No
  official code. 50.1x is from the abstract; per-spec success
  rates are unknown to this survey. whiteop is not confirmed as
  the DAC artifact.
- **Atelier Table IV cell values.** Prose claims (Atelier-3
  succeeds all runs; GPT-4/GLM-4 fail all) are in the paper
  text. The numeric grid did not extract cleanly.
- **Official code for** LADAC, AmpAgent, Atelier, ADO-LLM,
  AnalogAgent, AaLLM, AnalogTester, the 2604.07387 sizer.
  Absence of a GitHub hit is not proof they never released
  something under another name.
- **AnalogCoder GitHub README body.** The repo exists and the
  AAAI paper + a third-party leaderboard snapshot give the
  Pass@k numbers above. A raw README fetch in this session
  returned an empty markdown body; do not treat that as "the
  repo is empty".
- **AnalogGenie NSF text "73.5x more valid circuits".** The
  paper table is **73.5%** valid after augmented pretrain. The
  "x" wording looks like a PDF-extract error; I use the table.
- **AnalogCoder-Pro "28 circuits" vs AnalogCoder "24 tasks".**
  Pro's list includes later IDs (mixer, Wien, integrator). I
  did not re-count the published task list by hand beyond the
  abstract.
- **AaLLM, AnalogMaster, AnalogAgent, 2604.07387.** 2026
  preprints. Abstracts/HTML checked; no independent reproduction.
  AaLLM's 40x wall-clock and AnalogAgent's 97.4% Pass@1 should
  be treated as author-reported until someone reruns them.
- **PANDA == AnalogXpert.** PANDA cites AnalogXpert and ships
  an `analogxpert` tree; I did not audit that the ISEDA numbers
  reproduce from that repo.
- **Any system on a real foundry ADE netlist.** Unverified.
  Published loops use PySpice, HSPICE testbenches, Spectre on
  small amps, or SKY130.
- **Xyce.** Named in the ask; I did not find an LLM analog
  agent that calls Xyce as its primary engine.
- **Whether AnalogCoder-Pro's TCAD 2026 DOI page matches the
  arXiv tables byte-for-byte.** I used arXiv:2508.02518.

---

## Sources I actually opened

arXiv/HTML or PDF extracts: 2405.14918, 2508.02518, 2503.00205,
2407.18269, 2506.10235, 2406.18770, 2412.19824, 2409.14739,
2411.12930, 2410.20553, 2411.14299, 2509.25510, 2504.11497,
2512.22435, 2603.23910, 2608.13472, 2604.07387, 2604.20916,
2507.09965, 2308.16406, 2607.14165 (ATLAS ADC; used only as a
citation source). TechRxiv: LADAC, Atelier. ACM: Artisan
landing page. Boyd op-amp / GPCAD PDFs. GitHub fetches:
laiyao1/AnalogCoder, laiyao1/AnalogCoderPro, xz-group/AnalogGenie,
xz-group/AnalogSAGE, turtleben/LaMAGIC, dimplekochar/LEDRO,
eelab-dev/EEsizer, PKU-IDEA/PANDA, zehao-dong/CktGNN,
zhchenfdu/whiteop, jialinlu/OPAMP-Generator. This skill:
`SKILL.md`, `lib/netlist_digest.py`, `lib/spectre_to_ngspice.py`
(`tune` at line 1200).
