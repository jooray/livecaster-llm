"""Command line interface (SPEC §10)."""

from __future__ import annotations

import asyncio
import platform
import threading
import time
import webbrowser
from pathlib import Path
from typing import Annotated, Any

import typer
from dotenv import load_dotenv
from rich.console import Console
from rich.table import Table

from livecaster import __version__
from livecaster.config import Config, load_config
from livecaster.log import add_session_log, setup_logging
from livecaster.timeutil import fmt_hms

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Livecaster — a live podcast co-pilot.",
)
console = Console()

SetOpt = Annotated[list[str] | None, typer.Option("--set", help="Override a config key: section.key=value")]
ConfigOpt = Annotated[Path | None, typer.Option("--config", help="Path to livecaster.toml")]


def _version_callback(value: bool) -> None:
    if value:
        console.print(f"livecaster {__version__}")
        raise typer.Exit()


@app.callback()
def _main(
    version: Annotated[
        bool, typer.Option("--version", callback=_version_callback, is_eager=True, help="Print the version")
    ] = False,
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Debug logging")] = False,
) -> None:
    load_dotenv(override=False)
    setup_logging("DEBUG" if verbose else "INFO")


def _config(config: Path | None, overrides: list[str] | None) -> Config:
    return load_config(config, overrides)


def _client(cfg: Config, mock: bool, session_dir: Path | None, fixtures: Path | None = None) -> Any:
    from livecaster.llm.client import CallLog, LLMClient
    from livecaster.llm.mock import MockLLM

    call_log = CallLog(session_dir / "llm.jsonl" if session_dir else None)
    if mock:
        return MockLLM(fixtures, call_log=call_log)
    return LLMClient(cfg.llm.base_url, call_log=call_log, timeout=cfg.llm.tick_timeout_s)


# ---------------------------------------------------------------------------
# run
# ---------------------------------------------------------------------------


@app.command()
def run(
    outline: Annotated[Path, typer.Argument(help="Markdown outline (never modified)")],
    mode: Annotated[str, typer.Option(help="live | remote")] = "live",
    resume: Annotated[Path | None, typer.Option(help="Resume an existing session directory")] = None,
    slug: Annotated[str | None, typer.Option(help="Session directory slug")] = None,
    no_preflight: Annotated[bool, typer.Option("--no-preflight", help="Skip the pre-flight pass")] = False,
    mock_llm: Annotated[bool, typer.Option("--mock-llm", help="Use canned LLM responses")] = False,
    open_browser: Annotated[bool | None, typer.Option("--open/--no-open")] = None,
    config: ConfigOpt = None,
    set_: SetOpt = None,
) -> None:
    """Start a live session."""
    cfg = _config(config, set_)
    cfg.audio.mode = mode  # type: ignore[assignment]
    if no_preflight:
        cfg.llm.preflight = False
    if open_browser is not None:
        cfg.ui.open_browser = open_browser
    if not outline.is_file():
        console.print(f"[red]outline not found:[/red] {outline}")
        raise typer.Exit(2)
    asyncio.run(_run_session(outline, cfg, resume=resume, slug=slug, mock=mock_llm))


async def _run_session(
    outline: Path,
    cfg: Config,
    *,
    resume: Path | None,
    slug: str | None,
    mock: bool,
) -> None:
    from livecaster.engine import Engine, watch_outline
    from livecaster.llm.preflight import run_preflight
    from livecaster.session.store import create_session, load_session
    from livecaster.session.transcript import Transcript

    if resume:
        store = load_session(resume, cfg)
        store.session.status = "idle"
        console.print(f"[green]resuming[/green] {store.dir}")
    else:
        store = create_session(outline, cfg, mode=cfg.audio.mode, slug=slug)
        console.print(f"[green]session[/green] {store.dir}")
    add_session_log(store.dir)

    client = _client(cfg, mock, store.dir)
    engine = Engine(store, cfg, client)

    if resume:
        engine.transcript = Transcript.load_jsonl(
            store.transcript_path, [c.name for c in store.session.channels if c.is_direct]
        )
        engine.reasoner.transcript = engine.transcript
        last = engine.transcript.duration()
        engine.clock = type(engine.clock)(offset=last)  # continue the clock
        engine.reasoner.clock = engine.clock
        console.print(f"  {len(engine.transcript.segments)} segments, clock at {fmt_hms(last)}")

    if cfg.llm.preflight and store.session.preflight is None:
        console.print("running pre-flight…")
        await run_preflight(store, client, cfg)
        engine.fastlane.rebuild(store.outline, store.session.preflight)

    stop_watch = threading.Event()
    watch_outline(engine, stop_watch)
    try:
        await _serve(engine, cfg)
    finally:
        stop_watch.set()


async def _serve(engine: Any, cfg: Config) -> None:
    import uvicorn

    from livecaster.server.app import create_app

    application = create_app(engine)
    server = uvicorn.Server(
        uvicorn.Config(application, host=cfg.ui.host, port=cfg.ui.port, log_level="warning")
    )
    url = f"http://{'127.0.0.1' if cfg.ui.host == '0.0.0.0' else cfg.ui.host}:{cfg.ui.port}/"
    console.print(f"[bold]UI:[/bold] {url}")
    if cfg.ui.open_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    await server.serve()


# ---------------------------------------------------------------------------
# devices
# ---------------------------------------------------------------------------


@app.command()
def devices() -> None:
    """List audio input devices (and AudioTee candidates on macOS)."""
    from livecaster.audio.devices import audiotee_binary, audiotee_candidates, list_devices

    table = Table(title="Input devices")
    table.add_column("idx", justify="right")
    table.add_column("name")
    table.add_column("ch", justify="right")
    table.add_column("rate", justify="right")
    table.add_column("api")
    table.add_column("")
    for d in list_devices():
        table.add_row(
            str(d.index),
            d.name,
            str(d.max_input_channels),
            f"{d.default_samplerate:.0f}",
            d.hostapi,
            "default" if d.is_default else "",
        )
    console.print(table)
    console.print('Use them as [cyan]source = "device:<name or index>"[/cyan].')

    if platform.system() == "Darwin":
        candidates = audiotee_candidates()
        if candidates:
            t2 = Table(title="AudioTee candidates (per-process capture)")
            t2.add_column("app")
            t2.add_column("pids")
            for c in candidates:
                t2.add_row(c["name"], ", ".join(str(p) for p in c["pids"]))
            console.print(t2)
            console.print('Use them as [cyan]source = "audiotee:<app name>"[/cyan].')
        else:
            console.print("[dim]No AudioTee candidate processes running.[/dim]")
        if not audiotee_binary():
            console.print("[yellow]audiotee binary not built[/yellow] — run ./helpers/audiotee/build.sh")


# ---------------------------------------------------------------------------
# replay
# ---------------------------------------------------------------------------


@app.command()
def replay(
    transcript: Annotated[Path | None, typer.Option(help="transcript.jsonl to replay")] = None,
    wav: Annotated[Path | None, typer.Option(help="WAV file to replay through STT")] = None,
    session: Annotated[Path | None, typer.Option(help="Session directory to replay")] = None,
    outline: Annotated[Path | None, typer.Option(help="Outline to reason against")] = None,
    speed: Annotated[float, typer.Option(help="1 = real time, 0 = as fast as possible")] = 1.0,
    mock_llm: Annotated[bool, typer.Option("--mock-llm", help="Use canned LLM responses")] = False,
    fixtures: Annotated[Path | None, typer.Option(help="Mock response directory")] = None,
    serve: Annotated[bool, typer.Option("--serve", help="Open the UI while replaying")] = False,
    channel_name: Annotated[str, typer.Option(help="Channel name for a WAV replay")] = "Host",
    no_wrapup: Annotated[bool, typer.Option("--no-wrapup", help="Skip the wrap-up")] = False,
    config: ConfigOpt = None,
    set_: SetOpt = None,
) -> None:
    """Run the pipeline from a file instead of a microphone."""
    cfg = _config(config, set_)
    if session and not transcript:
        transcript = session / "transcript.jsonl"
        if outline is None:
            candidate = session / "outline.md"
            if candidate.is_file():
                outline = candidate
    if outline is None or not outline.is_file():
        console.print("[red]--outline is required[/red]")
        raise typer.Exit(2)
    if transcript is None and wav is None:
        console.print("[red]one of --transcript, --wav or --session is required[/red]")
        raise typer.Exit(2)

    if wav is not None:
        asyncio.run(_replay_wav(outline, wav, cfg, mock_llm, fixtures, speed, channel_name, serve))
        return
    asyncio.run(_replay_transcript(outline, transcript, cfg, mock_llm, fixtures, speed, serve, no_wrapup))


async def _replay_transcript(
    outline: Path,
    transcript: Path,
    cfg: Config,
    mock: bool,
    fixtures: Path | None,
    speed: float,
    serve: bool,
    no_wrapup: bool,
) -> None:
    from livecaster.engine import Engine
    from livecaster.replay import TranscriptReplay, build_replay_session, load_transcript_fixture
    from livecaster.timeutil import ManualClock

    segments = load_transcript_fixture(transcript)
    channels = sorted({s.channel for s in segments})
    store = build_replay_session(outline, cfg, channels=channels)
    for ch in store.session.channels:
        ch.is_direct = ch.name.lower() != "host"
    add_session_log(store.dir)
    console.print(f"[green]session[/green] {store.dir} · {len(segments)} segments")
    client = _client(cfg, mock, store.dir, fixtures)
    clock = ManualClock()
    engine = Engine(store, cfg, client, clock=clock)

    def show(kind: str, payload: Any) -> None:
        if kind != "patch":
            return
        bits = []
        for node_id, st in payload.nodes.items():
            if st.status != "untouched":
                bits.append(f"{node_id}={st.status}")
            if st.hot:
                bits.append(f"{node_id}~hot{st.hot.score:.1f}")
            elif st.warm and st.status == "untouched":
                bits.append(f"{node_id}~warm{st.warm:.1f}")
        if bits:
            console.print("[dim]patch[/dim] " + " ".join(bits))

    store.subscribe(show)
    await engine.startup()
    await engine.reasoner.stop()
    replay_task = TranscriptReplay(engine, segments, speed=speed)

    if serve:
        runner = asyncio.create_task(replay_task.run())
        await _serve(engine, cfg)
        await runner
        return

    await replay_task.run()
    if not no_wrapup:
        await engine.finish()
    else:
        store.session.status = "finished"
        store.snapshot(force=True)
    await engine.shutdown()
    _print_summary(store)


async def _replay_wav(
    outline: Path,
    wav: Path,
    cfg: Config,
    mock: bool,
    fixtures: Path | None,
    speed: float,
    channel_name: str,
    serve: bool,
) -> None:
    from livecaster.config import ChannelConfig
    from livecaster.engine import Engine
    from livecaster.session.store import create_session

    cfg = cfg.model_copy(deep=True)
    cfg.audio.channels = [ChannelConfig(name=channel_name, source=f"file:{wav.resolve()}", record=False)]
    cfg.audio.record = False
    store = create_session(outline, cfg, mode="replay")
    add_session_log(store.dir)
    console.print(f"[green]session[/green] {store.dir}")
    client = _client(cfg, mock, store.dir, fixtures)

    done = asyncio.Event()
    started = time.monotonic()
    engine = Engine(
        store,
        cfg,
        client,
        replay_speed=speed,
        autostart=True,
        on_replay_finished=done.set,
    )
    await engine.startup()
    if serve:
        asyncio.create_task(_serve(engine, cfg))
    await done.wait()
    await asyncio.sleep(1.0)
    audio_s = getattr(engine.channels[0].source, "duration_s", 0.0) if engine.channels else 0.0
    await engine.finish()
    await engine.shutdown()
    elapsed = time.monotonic() - started
    for seg in engine.transcript.segments:
        console.print(f"[dim]{fmt_hms(seg.t0)}[/dim] {seg.text}")
    if audio_s:
        console.print(f"\nreal-time factor: [bold]{elapsed / audio_s:.2f}[/bold] ({audio_s:.1f}s audio)")
    _print_summary(store)


def _print_summary(store: Any) -> None:
    from livecaster.session.reducer import coverage_summary

    s = store.session
    cov = coverage_summary(s, store.outline)
    console.print(
        f"\n[bold]{s.id}[/bold] · {fmt_hms(s.duration_s)} · "
        f"covered {cov['covered']}/{cov['total']} · touched {cov['touched']} · skipped {cov['skipped']}"
    )
    console.print(
        f"language={s.language} · ticks={s.usage.ticks} · failures={s.usage.failures} · "
        f"${s.usage.cost_usd:.4f} · mentions={len(s.mentions)}"
    )
    for name, path in (s.final_paths or {}).items():
        console.print(f"  {name}: {path}")


# ---------------------------------------------------------------------------
# wrapup / export
# ---------------------------------------------------------------------------


@app.command()
def wrapup(
    session_dir: Annotated[Path, typer.Argument(help="Session directory")],
    model: Annotated[str | None, typer.Option(help="Override the final model")] = None,
    resolve_links: Annotated[bool, typer.Option("--resolve-links", help="Look up missing URLs")] = False,
    mock_llm: Annotated[bool, typer.Option("--mock-llm")] = False,
    config: ConfigOpt = None,
    set_: SetOpt = None,
) -> None:
    """Re-run the wrap-up on an existing session."""
    cfg = _config(config, set_)
    asyncio.run(_wrapup(session_dir, cfg, model, resolve_links, mock_llm))


async def _wrapup(session_dir: Path, cfg: Config, model: str | None, resolve: bool, mock: bool) -> None:
    from livecaster.llm.wrapup import run_wrapup
    from livecaster.session.store import load_session
    from livecaster.session.transcript import Transcript

    store = load_session(session_dir, cfg)
    transcript = Transcript.load_jsonl(
        store.transcript_path, [c.name for c in store.session.channels if c.is_direct]
    )
    client = _client(cfg, mock, store.dir)
    console.print(f"wrapping up {store.dir} ({len(transcript.segments)} segments)…")
    paths = await run_wrapup(store, transcript, client, cfg, model=model, resolve=resolve or None)
    await client.aclose()
    for name, path in paths.items():
        console.print(f"  {name}: {path}")


@app.command()
def export(session_dir: Annotated[Path, typer.Argument(help="Session directory")]) -> None:
    """Re-render the Markdown and SRT exports without calling the LLM."""
    from livecaster.llm.wrapup import export_only
    from livecaster.session.store import load_session
    from livecaster.session.transcript import Transcript

    store = load_session(session_dir)
    transcript = Transcript.load_jsonl(
        store.transcript_path, [c.name for c in store.session.channels if c.is_direct]
    )
    paths = export_only(store, transcript)
    for name, path in paths.items():
        console.print(f"  {name}: {path}")


# ---------------------------------------------------------------------------
# check
# ---------------------------------------------------------------------------


@app.command()
def check(
    stt: Annotated[bool, typer.Option("--stt/--no-stt", help="Run the STT self-test")] = True,
    sample: Annotated[Path | None, typer.Option(help="WAV for the STT self-test")] = None,
    config: ConfigOpt = None,
    set_: SetOpt = None,
) -> None:
    """Verify the Venice key and models, the STT engine, audio devices and permissions."""
    cfg = _config(config, set_)
    ok = asyncio.run(_check(cfg, stt, sample))
    raise typer.Exit(0 if ok else 1)


async def _check(cfg: Config, run_stt: bool, sample: Path | None) -> bool:
    import os
    import shutil

    ok = True
    console.rule("Venice")
    key = os.environ.get("VENICE_API_KEY", "").strip()
    if not key:
        console.print("[red]VENICE_API_KEY is not set[/red] — export it or add it to .env")
        ok = False
    else:
        console.print(f"key present ({len(key)} chars)")
        from livecaster.llm.client import LLMClient
        from livecaster.llm.pricing import price_for, refresh_from_models

        client = LLMClient(cfg.llm.base_url)
        try:
            payload = await client.list_models()
            refresh_from_models(payload)
            ids = {m.get("id") for m in payload.get("data", [])}
            table = Table("model", "input $/1M", "cached $/1M", "output $/1M", "available")
            for name in {cfg.llm.tick_model, cfg.llm.final_model}:
                p = price_for(name)
                present = name in ids
                ok = ok and present
                table.add_row(
                    name,
                    f"{p.input:.4f}",
                    f"{p.cached_input:.4f}",
                    f"{p.output:.4f}",
                    "[green]yes[/green]" if present else "[red]NO[/red]",
                )
            console.print(table)
        except Exception as exc:
            console.print(f"[red]Venice request failed:[/red] {exc}")
            ok = False
        finally:
            await client.aclose()

    console.rule("Audio devices")
    try:
        from livecaster.audio.devices import list_devices

        found = list_devices()
        for d in found[:12]:
            console.print(
                f"  [{d.index}] {d.name} · {d.max_input_channels}ch · {d.default_samplerate:.0f} Hz"
            )
        if not found:
            console.print("[red]no input devices found[/red]")
            ok = False
    except Exception as exc:
        console.print(f"[red]device enumeration failed:[/red] {exc}")
        ok = False

    if platform.system() == "Darwin":
        console.rule("macOS")
        from livecaster.audio.devices import audiotee_binary

        binary = audiotee_binary()
        if binary:
            console.print(f"audiotee: {binary}")
        else:
            console.print(
                "[yellow]audiotee not built[/yellow] — run ./helpers/audiotee/build.sh, "
                'or use BlackHole with source = "device:BlackHole 2ch"'
            )
        console.print(
            "Permissions: grant your terminal app [bold]Microphone[/bold] and "
            "[bold]Screen & System Audio Recording[/bold] in System Settings → Privacy & Security. "
            "The first capture triggers the prompt; some terminals never raise it — then use BlackHole."
        )

    console.rule("Disk")
    sessions = Path(cfg.session.dir)
    sessions.mkdir(parents=True, exist_ok=True)
    usage = shutil.disk_usage(sessions)
    free_gb = usage.free / 1e9
    console.print(f"{sessions.resolve()} — {free_gb:.1f} GB free")
    if free_gb < 1.0:
        console.print("[yellow]less than 1 GB free[/yellow]")

    if run_stt:
        console.rule("STT")
        ok = _check_stt(cfg, sample) and ok
    return ok


def _check_stt(cfg: Config, sample: Path | None) -> bool:
    import numpy as np

    from livecaster.stt.registry import select_engine

    try:
        engine = select_engine(cfg.stt.engine, cfg.stt.model, cfg.stt.language)
    except Exception as exc:
        console.print(f"[red]engine selection failed:[/red] {exc}")
        return False
    console.print(f"engine: [bold]{engine.name}[/bold]")
    started = time.monotonic()
    try:
        engine.warmup()
    except Exception as exc:
        console.print(f"[red]warmup failed:[/red] {exc}")
        return False
    console.print(f"model loaded in {time.monotonic() - started:.1f}s")

    path = sample or Path("tests/fixtures/speech_sk_30s.wav")
    if not path.is_file():
        console.print(f"[yellow]no sample at {path}[/yellow] — record one with:")
        console.print(
            '  ffmpeg -f avfoundation -i ":0" -t 30 -ar 16000 -ac 1 tests/fixtures/speech_sk_30s.wav'
        )
        console.print("running a 3 s silence self-test instead")
        engine.transcribe(np.zeros(48_000, dtype=np.float32), None)
        console.print("[green]engine responds[/green]")
        return True

    import soundfile as sf

    audio, rate = sf.read(str(path), dtype="float32", always_2d=True)
    mono = audio.mean(axis=1)
    if rate != 16_000:
        import soxr

        mono = soxr.resample(mono, rate, 16_000)
    duration = len(mono) / 16_000
    started = time.monotonic()
    result = engine.transcribe(mono, None if cfg.stt.language == "auto" else cfg.stt.language)
    elapsed = time.monotonic() - started
    console.print(
        f"[dim]{path}[/dim] {duration:.1f}s audio in {elapsed:.1f}s "
        f"(RTF [bold]{elapsed / max(duration, 0.01):.2f}[/bold])"
    )
    console.print(f"language={result.language} words={len(result.words)}")
    console.print(result.text or "[red]empty transcript[/red]")
    return bool(result.text)


def main() -> None:
    app()


if __name__ == "__main__":  # pragma: no cover
    main()
