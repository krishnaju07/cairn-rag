import argparse
import json
import os
import re
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parents[1]
HERE = Path(__file__).parent
MODES = ("semantic", "keyword", "hybrid")
MARKER = re.compile(r"\[(\d+)\]")
DECLINED = re.compile(
    r"\b(?:not|no|n't|cannot|unable)\b.{0,60}\b(?:listed|mentioned|provided|stated|given|available|specified|found|include)", re.I
)
JUDGE_SYSTEM = 'You check whether an answer is supported by passages. Reply with JSON only: {"supported": true or false}.'


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Retrieval hit rate, citation accuracy and answer faithfulness on a Q&A set.")
    parser.add_argument("--suite", default="basic", help="basic, hard or hard/scanned (a folder under eval/)")
    parser.add_argument("--model", default="auto", help="auto, extractive, ollama:<name>, nvidia:<name> or anthropic:<name>")
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--ocr", action="store_true", help="read PDFs with LlamaParse (uses LlamaCloud credits)")
    parser.add_argument(
        "--judge",
        choices=("lexical", "llm", "both"),
        default="lexical",
        help="llm and both also ask the answer model to grade; small models grade unreliably",
    )
    parser.add_argument("--embed-model", default=None)
    parser.add_argument("--retrieval-only", action="store_true", help="skip answer generation")
    parser.add_argument("--chunk-size", type=int, default=None)
    parser.add_argument("--detail-size", type=int, default=None, help="0 turns detail passages off")
    parser.add_argument("--output", type=Path, default=None)
    return parser.parse_args()


def prepare_environment() -> Path:
    scratch = Path(tempfile.mkdtemp(prefix="cairn-eval-"))
    cached = ROOT / "data" / "models"
    if cached.exists():
        (scratch / "models").symlink_to(cached)
    os.environ["DATA_DIR"] = str(scratch)
    os.environ["ENV_FILE"] = str(scratch / ".env")
    if os.environ.get("EVAL_KEEP_ENV") != "1" and (ROOT / ".env").exists():
        (scratch / ".env").write_text((ROOT / ".env").read_text())
    sys.path.insert(0, str(ROOT))
    return scratch


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower()).strip()


def documents_of(question: dict[str, Any]) -> list[str]:
    return question["doc"] if isinstance(question["doc"], list) else [question["doc"]]


def covers_page(hit, question: dict[str, Any]) -> bool:
    return "page" not in question or hit.page_start <= question["page"] <= hit.page_end


def is_hit(hit, question: dict[str, Any]) -> bool:
    return hit.filename in documents_of(question) and normalize(question["contains"]) in normalize(hit.text)


def build_collection(model: str, top_k: int, docs: Path, ocr: bool, chunk: dict[str, int], embed_model: Optional[str]):
    from app import library
    from app.ingestion import pipelines, staging

    pipeline = pipelines.create("Evaluation", "standard")
    steps = pipelines.draft_steps(pipeline["id"])
    if ocr:
        steps[0]["settings"].update(engine="llamaparse", tier="fast")
    steps[1]["settings"].update(chunk)
    if embed_model:
        steps[2]["settings"]["model"] = embed_model
    pipelines.save_draft(pipeline["id"], steps)
    pipeline = pipelines.publish(pipeline["id"])
    collection = library.create_collection("Evaluation", pipeline["id"], 1, model, "hybrid", top_k)
    files = sorted(path for path in docs.iterdir() if path.is_file())
    if not files:
        sys.exit(f"No documents in {docs}. Run the suite's build.py first.")
    for path in files:
        library.add_document(collection["id"], staging.stage(path.name, path.read_bytes(), ocr)["stage_id"])
    while any(doc["status"] in ("queued", "processing") for doc in library.list_documents(collection["id"])):
        time.sleep(0.5)
    failed = [doc for doc in library.list_documents(collection["id"]) if doc["status"] != "ready"]
    if failed:
        sys.exit("Processing failed: " + "; ".join(f"{doc['filename']}: {doc['error']}" for doc in failed))
    return library.get_collection(collection["id"])


def retrieval_report(collection, questions: list[dict[str, Any]], top_k: int) -> dict[str, dict[str, float]]:
    from app.retrieval import search

    answerable = [q for q in questions if q.get("answerable", True)]
    report = {}
    for mode in MODES:
        found, on_page, reciprocal = 0, 0, 0.0
        for question in answerable:
            ranked = search.search(collection, question["question"], mode, top_k)
            position = next((index for index, hit in enumerate(ranked, start=1) if is_hit(hit, question)), None)
            found += position is not None
            on_page += any(is_hit(hit, question) and covers_page(hit, question) for hit in ranked)
            reciprocal += 1 / position if position else 0.0
        report[mode] = {
            "hit_rate": found / len(answerable),
            "page_rate": on_page / len(answerable),
            "mrr": reciprocal / len(answerable),
        }
    return report


def lexical_support(answer: dict[str, Any]) -> float:
    from app.generation.answer import grounding
    from app.retrieval.search import Hit

    cited = {int(n) for n in MARKER.findall(answer["answer"])}
    passages = [
        Hit(0, "", c["filename"], "chunk", c["text"], 1, 1, 0, 0, True, 0.0) for c in answer["citations"] if c["n"] in cited
    ]
    return grounding(answer["answer"], passages) if cited else 0.0


def llm_support(spec: str, answer: dict[str, Any]) -> float:
    from app.generation import providers

    passages = "\n\n".join(f"[{c['n']}] {c['text']}" for c in answer["citations"])
    prompt = f"Passages:\n{passages}\n\nAnswer:\n{answer['answer']}\n\nIs every claim in the answer supported by the passages?"
    reply = providers.complete(spec, JUDGE_SYSTEM, prompt, 40)
    return 1.0 if re.search(r'"supported"\s*:\s*true', reply, re.I) else 0.0


def cites_page(result: dict[str, Any], question: dict[str, Any]) -> bool:
    return any(
        c["filename"] in documents_of(question) and covers_page(type("Span", (), c), question) for c in result["citations"]
    )


def answer_report(collection, questions: list[dict[str, Any]], judge: str) -> dict[str, Any]:
    from app.generation import answer

    spec = collection["answer_model"]
    grounded, graded, correct, paged, refused, unanswerable, answered_count, latencies, rows = [], [], 0, 0, 0, 0, 0, [], []
    for question in questions:
        result = answer.ask(collection, question["question"], "eval")
        latencies.append(result["latency_ms"])
        row = {"question": question["question"], "answered": result["answered"], "answer": result["answer"]}
        if question.get("answerable", True):
            if result["answered"]:
                answered_count += 1
                row["grounding"] = lexical_support(result)
                grounded.append(row["grounding"])
                said = normalize(result["answer"])
                row["has_fact"] = any(normalize(phrase) in said for phrase in question.get("says", [question["contains"]]))
                correct += row["has_fact"]
                if "page" in question:
                    row["cites_right_page"] = cites_page(result, question)
                    paged += row["cites_right_page"]
                if judge in ("llm", "both"):
                    row["judge"] = llm_support(spec, result)
                    graded.append(row["judge"])
        else:
            unanswerable += 1
            declined = not result["answered"] or bool(DECLINED.search(result["answer"]))
            refused += declined
            row["declined"] = declined
            row["expected"] = "refusal"
        rows.append(row)
    answerable = sum(1 for q in questions if q.get("answerable", True))
    with_pages = sum(1 for q in questions if q.get("answerable", True) and "page" in q)
    return {
        "answered_rate": answered_count / answerable,
        "states_the_fact": correct / answerable,
        "cites_right_page": paged / with_pages if with_pages else None,
        "grounding": sum(grounded) / len(grounded) if grounded else 0.0,
        "llm_judge": sum(graded) / len(graded) if graded else None,
        "refusal_accuracy": refused / unanswerable if unanswerable else None,
        "median_latency_ms": sorted(latencies)[len(latencies) // 2],
        "rows": rows,
    }


def wait_until_ready(spec: str, seconds: int = 180) -> None:
    from app.generation import providers

    deadline = time.monotonic() + seconds
    while not providers.is_ready(spec):
        if time.monotonic() > deadline:
            sys.exit(f"{spec} is not available. Check Providers in the app.")
        time.sleep(2)


def percent(value: float) -> str:
    return f"{value:.0%}"


def main() -> None:
    args = parse_arguments()
    suite = HERE / args.suite
    scratch = prepare_environment()
    from app import db
    from app.generation import providers

    db.init()

    model = args.model if args.model != "auto" else (providers.default_llm() or providers.EXTRACTIVE)
    wait_until_ready(model)
    questions = [json.loads(line) for line in (suite / "questions.jsonl").read_text().splitlines() if line.strip()]
    print(f"Indexing {args.suite} in {scratch} ...")
    started = time.perf_counter()
    chunk = {key: value for key, value in (("size", args.chunk_size), ("detail_size", args.detail_size)) if value is not None}
    collection = build_collection(model, args.top_k, suite / "docs", args.ocr, chunk, args.embed_model)
    print(
        f"{collection['ready']} documents, {collection['chunks']} chunks in {time.perf_counter() - started:.0f}s. "
        f"Answer model: {model}\n"
    )

    retrieval = retrieval_report(collection, questions, args.top_k)
    print(f"Retrieval on {sum(1 for q in questions if q.get('answerable', True))} answerable questions (top {args.top_k})")
    print(f"{'mode':<10}{'hit rate':>10}{'right page':>12}{'MRR':>8}")
    for mode, values in retrieval.items():
        print(f"{mode:<10}{percent(values['hit_rate']):>10}{percent(values['page_rate']):>12}{values['mrr']:>8.2f}")

    if args.retrieval_only:
        return
    answers = answer_report(collection, questions, args.judge)
    print(f"\nAnswers with {model} ({collection['retrieval_mode']} retrieval)")
    print(f"answered when answerable : {percent(answers['answered_rate'])}")
    print(f"states the right fact    : {percent(answers['states_the_fact'])}")
    if answers["cites_right_page"] is not None:
        print(f"cites the right page     : {percent(answers['cites_right_page'])}")
    print(
        f"grounding                : {percent(answers['grounding'])}  "
        "(share of the answer's content words found in the passages it cites)"
    )
    if answers["llm_judge"] is not None:
        print(
            f"faithfulness (LLM judge) : {percent(answers['llm_judge'])}  "
            "(the answer model grading itself; treat small models with caution)"
        )
    if answers["refusal_accuracy"] is not None:
        print(f"declined when unsupported: {percent(answers['refusal_accuracy'])}")
    print(f"median latency           : {answers['median_latency_ms']} ms")
    for row in answers["rows"]:
        if row.get("expected") == "refusal" and not row["declined"]:
            print(f"  NOT REFUSED : {row['question']}")
        elif "expected" not in row and (not row["answered"] or not row.get("has_fact") or row.get("cites_right_page") is False):
            print(f"  MISSED      : {row['question']} -> {'refused' if not row['answered'] else row['answer'][:110]!r}")

    output = args.output or scratch / "results.json"
    output.write_text(
        json.dumps(
            {"suite": args.suite, "model": model, "top_k": args.top_k, "retrieval": retrieval, "answers": answers}, indent=2
        )
    )
    print(f"\nDetails written to {output}")


if __name__ == "__main__":
    main()
