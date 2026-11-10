"""Step 1 before anything else: check that your API key and model work.

Usage:  python -m scripts.check_llm

It (1) lists the Gemini models your key can use, (2) sends one short message, (3) checks that the
model can call a tool (needed by the agent), and (4) estimates how many requests the full
evaluation needs, so you can compare with your free-tier daily limit.
"""
import sys

from src.config import GEMINI_API_KEY, GEMINI_MODEL, LLM_PROVIDER, LLM_REQUESTS_PER_MINUTE
from src.llm import LLMError, ToolSpec, current_model_name, make_session


def main():
    print(f"Provider: {LLM_PROVIDER} | model: {current_model_name()}")
    if LLM_PROVIDER == "gemini":
        if not GEMINI_API_KEY:
            print("\nGEMINI_API_KEY is empty. Copy .env.example to .env and paste your key there.")
            return 1
        print(f"Key: {GEMINI_API_KEY[:4]}...{GEMINI_API_KEY[-4:]} ({len(GEMINI_API_KEY)} chars)")
        from src.llm.gemini import list_models
        try:
            models = list_models()
        except LLMError as e:
            print(f"\nFAILED to list models: {e}\nCheck the key in .env (no quotes, no spaces).")
            return 1
        names = [m["name"].replace("models/", "") for m in models]
        skip = ("image", "tts", "audio", "live", "embedding", "omni")
        flash = sorted((n for n in names if "flash" in n and not any(k in n for k in skip)), reverse=True)
        print(f"\nYour key can use {len(names)} models. Text Flash models (the free-tier ones), newest first:")
        for n in flash:
            print(f"  - {n}")
        if GEMINI_MODEL not in names:
            print(f"\nWARNING: GEMINI_MODEL '{GEMINI_MODEL}' is not in your list. "
                  f"Set GEMINI_MODEL in .env to one of the flash models above.")

    print("\n[1/2] Plain message (can take up to a minute) ...", end=" ", flush=True)
    try:
        r = make_session("Reply with exactly: OK").send(user_text="Say OK")
        print(f"reply={r.text[:40]!r} ({r.seconds:.1f}s, {r.input_tokens} in / {r.output_tokens} out tokens)")
    except LLMError as e:
        print(f"FAILED\n{e}")
        return 1

    print("[2/2] Tool call ...", end=" ", flush=True)
    tool = ToolSpec("run_sql", "Run a SQL query.", {"type": "object", "properties": {
        "sql": {"type": "string", "description": "SQL"}}, "required": ["sql"]})
    try:
        r = make_session("Use the run_sql tool to answer.", [tool]).send(
            user_text="How many rows are in the table patients? Call run_sql.")
        if r.tool_calls:
            print(f"OK, model called {r.tool_calls[0].name}({r.tool_calls[0].args})")
        else:
            print(f"model answered without calling the tool: {r.text[:80]!r}. "
                  "The agent may struggle with this model; try another flash model.")
    except LLMError as e:
        print(f"FAILED\n{e}")
        return 1

    est = 50 * 2 + 50 * 5
    minutes = est / LLM_REQUESTS_PER_MINUTE if LLM_REQUESTS_PER_MINUTE else 0
    print(f"\nAll good. The full evaluation needs about {est} requests (2 baselines x 50 + agent ~5 x 50).")
    print(f"At LLM_REQUESTS_PER_MINUTE={LLM_REQUESTS_PER_MINUTE:g} that is about {minutes:.0f} minutes.")
    print("Check your model's requests-per-day limit in Google AI Studio (Usage / Rate limits). If it is lower,\n"
          "the evaluation stops when the quota runs out; run the same command the next day to continue.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
