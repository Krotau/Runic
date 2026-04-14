from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

import polars as pl

from runic import DefaultError, Err, InMemoryTaskBackend, Ok, Query, Runic
from runic.result import Result


DATA_PATH = Path(__file__).parent / "data" / "fake.csv"


@dataclass(slots=True)
class SampleStdDev(Query[dict[str, float | int | str], DefaultError]):
    sample_size: int
    column: str = "value"
    segment: str | None = None


@dataclass(slots=True)
class StoreSampleRun:
    run_id: int
    sample_size: int
    segment: str | None = None


class StatisticsService:
    def __init__(self, csv_path: Path) -> None:
        self._csv_path = csv_path

    async def ask(self, query: SampleStdDev) -> Result[dict[str, float | int | str], DefaultError]:
        if query.sample_size <= 1:
            return Err(DefaultError(message="sample_size must be greater than 1", code="invalid_sample"))
        if not self._csv_path.exists():
            return Err(DefaultError(message=f"Missing data file: {self._csv_path}", code="missing_file"))
        return await asyncio.to_thread(self._sample_std_dev, query)

    def _sample_std_dev(self, query: SampleStdDev) -> Result[dict[str, float | int | str], DefaultError]:
        frame = pl.read_csv(self._csv_path)
        if query.column not in frame.columns:
            return Err(DefaultError(message=f"Unknown column: {query.column}", code="unknown_column"))

        if query.segment is not None:
            frame = frame.filter(pl.col("segment") == query.segment)
            if frame.height == 0:
                return Err(DefaultError(message=f"No rows found for segment: {query.segment}", code="missing_segment"))

        if query.sample_size > frame.height:
            return Err(
                DefaultError(
                    message=f"sample_size {query.sample_size} is larger than available rows {frame.height}",
                    code="sample_too_large",
                )
            )

        sample = frame.sample(n=query.sample_size, shuffle=True)
        std_dev = sample.select(pl.col(query.column).std()).item()
        mean = sample.select(pl.col(query.column).mean()).item()

        return Ok(
            {
                "column": query.column,
                "segment": query.segment or "all",
                "sample_size": query.sample_size,
                "mean": round(float(mean), 4),
                "std_dev": round(float(std_dev), 4),
            }
        )


async def main() -> None:
    backend = InMemoryTaskBackend()
    runic = Runic(task_backend=backend)
    handler = runic.register(StatisticsService(DATA_PATH))

    @runic.task(StoreSampleRun)
    async def store_sample_run(ctx, req: StoreSampleRun) -> Result[Mapping[str, object], DefaultError]:
        result = await handler.ask(SampleStdDev(sample_size=req.sample_size, segment=req.segment))
        if isinstance(result, Err):
            return result
        assert isinstance(result, Ok)

        history = list(ctx.shared.get("history", []))
        history.append({"run_id": req.run_id, **result.value})
        ctx.shared["history"] = history
        ctx.shared["last_run"] = req.run_id
        await ctx.log(f"stored run {req.run_id}")
        return Ok({"stored": True, "run_id": req.run_id})

    print(f"loading {DATA_PATH}")

    for run in range(1, 4):
        result = await handler.ask(SampleStdDev(sample_size=5))
        print(f"run {run}:", result)
        await runic.start(StoreSampleRun(run_id=run, sample_size=5))

    segment_result = await handler.ask(SampleStdDev(sample_size=4, segment="b"))
    print("segment b:", segment_result)

    while True:
        history = backend.shared.get("history", [])
        if len(history) >= 3:
            break
        await asyncio.sleep(0.01)

    print("shared backend state:", backend.shared)


if __name__ == "__main__":
    asyncio.run(main())
