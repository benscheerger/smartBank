from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

from automation.evidence import ModelCallMetadata
from automation.terminal import model_call_lines, run_cli


def main() -> None:
    key_file = (
        Path.home()
        / ".config"
        / "computer-use-automation"
        / ".env"
    )
    load_dotenv(key_file, override=True)

    client = OpenAI(max_retries=0, timeout=30.0)
    response = client.responses.create(
        model="gpt-6-luna",
        input="Reply with exactly: API connection works.",
        reasoning={"effort": "none"},
        max_output_tokens=50,
        store=False,
    )

    if response.output_text.strip() != "API connection works.":
        raise RuntimeError("The API check returned unexpected content.")

    usage = response.usage
    metadata = ModelCallMetadata(
        model=response.model,
        response_id=response.id,
        input_tokens=(usage.input_tokens if usage else None),
        output_tokens=(usage.output_tokens if usage else None),
        total_tokens=(usage.total_tokens if usage else None),
    )

    print("API connection works.")

    for line in model_call_lines(metadata):
        print(line)


if __name__ == "__main__":
    run_cli(main)
