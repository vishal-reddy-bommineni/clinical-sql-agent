# Run guide (Windows PowerShell)

## 1. One-time setup
```powershell
cd "C:\Users\vineel reddy\Downloads\clinical-sql-agent"
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
notepad .env
```
In Notepad, replace `paste-your-key-here` with your Gemini key (no quotes, no spaces), save, close.
The dataset is already in `data/raw/`; the database builds itself on first use.

## 2. Check the tests pass (no API key used)
```powershell
python -m pytest -q
```
Expect `41 passed`.

## 3. Check your key and model
```powershell
python -m scripts.check_llm
```
It lists the Gemini models your key can use, sends one message and tests a tool call.
- If it says your `GEMINI_MODEL` is not in the list, open `.env` and set `GEMINI_MODEL=` to one of the
  listed **flash** models, then run the check again.
- If it says the model answered without calling the tool, pick another flash model.
- If you get 429 errors, lower `LLM_REQUESTS_PER_MINUTE` in `.env` (e.g. to 5).

## 4. Quick evaluation test (3 questions, about 25 requests)
```powershell
python -m src.evaluation.run_eval --ids q01 q23 q45 --run-name smoke
```
You should see lines like `[1/9] OK q01 baseline_schema ...`. Any `ERR` with "LLM error" means a
key/model problem; paste the output to Claude.

## 5. Full evaluation (about 350 requests)
```powershell
python -m src.evaluation.run_eval
```
- Each finished question is saved immediately to `reports/runs/<model>/results.jsonl`.
- If it stops with "daily quota reached", run **the same command** again after the quota resets
  (midnight Pacific time). It continues where it stopped.
- When everything is done it writes `reports/RESULTS.md` and two charts in `reports/figures/`.

Then send Claude `reports/RESULTS.md` and `reports/runs/<model>/results.jsonl` to write the README
results, analysis and resume bullets.

## 6. The app
```powershell
streamlit run app.py
```
Opens in your browser. Tabs: **Ask** (type a question or click an example), **Evaluation**
(your results), **Data guide** (every table explained). Stop with Ctrl+C.

## 7. GitHub
Create an empty repo `clinical-sql-agent` (no README), then:
```powershell
git init
git add .
git commit -m "Clinical SQL agent on MIMIC-IV demo with 50-question evaluation"
git branch -M main
git remote add origin https://github.com/vishal-reddy-bommineni/clinical-sql-agent.git
git push -u origin main
```
`.env` (your key) and the built database are in `.gitignore` and will not be uploaded.

## 8. Deploy on Streamlit Community Cloud
share.streamlit.io -> Create app -> your repo, branch `main`, main file `app.py`.
Advanced settings -> Python 3.13, and in **Secrets** paste:
```toml
GEMINI_API_KEY = "your-key"
GEMINI_MODEL = "the-model-that-worked-in-step-3"
LLM_REQUESTS_PER_MINUTE = "8"
```
Deploy, then put the app URL in the README badge and in the repo's About -> Website.
Anyone using your public app spends your free quota; that's fine for a portfolio demo.
