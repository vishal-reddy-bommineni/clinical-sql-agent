# Run guide

Commands are shown for Windows PowerShell; on macOS/Linux use `source .venv/bin/activate` and `cp`
instead of `.venv\Scripts\Activate.ps1` and `Copy-Item`.

## 1. Setup
```powershell
git clone https://github.com/vishal-reddy-bommineni/clinical-sql-agent.git
cd clinical-sql-agent
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
notepad .env
```
In `.env`, replace `paste-your-key-here` with a Gemini API key from Google AI Studio (no quotes, no
spaces) and save. The dataset is already in `data/raw/`; the database builds itself on first use.

## 2. Tests and data checks (no API key needed)
```powershell
python -m pytest -q                 # 43 tests, including a mock Gemini server
python -m scripts.profile_data      # data-profiling queries -> reports/profiling.md
python -m src.evaluation.gold       # answer key -> reports/gold_answers.md
```

## 3. Check the key and model
```powershell
python -m scripts.check_llm
```
Lists the Gemini models the key can use, sends one message and tests a tool call.
- If `GEMINI_MODEL` is not in the list, set it in `.env` to one of the listed Flash models.
- If the model answers without calling the tool, choose another Flash model.
- On 429 errors, lower `LLM_REQUESTS_PER_MINUTE` in `.env`.

Free-tier limits differ by model (see Usage / Rate limits in Google AI Studio). The full evaluation
needs about 350 requests; the published results used `gemini-3.5-flash-lite` (500 requests/day).

## 4. Evaluation
```powershell
python -m src.evaluation.run_eval --ids q01 q23 q45 --run-name smoke   # quick trial
python -m src.evaluation.run_eval                                      # all 50 questions x 3 approaches
```
- Every finished answer is saved immediately to `reports/runs/<model>/results.jsonl`.
- If the daily quota runs out, run the same command after it resets (midnight Pacific time); it
  continues where it stopped.
- At the end it writes `reports/RESULTS.md` and the charts in `reports/figures/`.

Other evaluation commands:
```powershell
python -m src.evaluation.report                      # rebuild RESULTS.md and charts
python -m src.evaluation.rescore                     # re-score saved answers after fixing the answer key
python -m src.evaluation.run_eval --ids q36 --redo   # rerun chosen questions
```

## 5. App
```powershell
streamlit run app.py
```
Tabs: **Ask** (type a question or click an example), **Evaluation**, **Data guide**. Stop with Ctrl+C.

## 6. Deploy on Streamlit Community Cloud
share.streamlit.io -> Create app -> this repository, branch `main`, main file `app.py`.
Under Advanced settings choose Python 3.13 and add to **Secrets**:
```toml
GEMINI_API_KEY = "your-key"
GEMINI_MODEL = "gemini-3.5-flash-lite"
LLM_REQUESTS_PER_MINUTE = "12"
```
Visitors to a public app use the key's free quota.
