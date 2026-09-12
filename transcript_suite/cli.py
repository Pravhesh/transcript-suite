"""
Command Line Interface for Transcript Suite.
"""

import os
from pathlib import Path
from typing import Optional

# Prevent CUDA memory fragmentation on 8GB GPUs
os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")

import typer
from rich.console import Console, Group
from rich.panel import Panel
from rich.table import Table
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TimeElapsedColumn
import torch

from .config import config
from .asr.memory import VRAMManager

app = typer.Typer(
    name="transcript-suite",
    help="High-performance transcription suite powered by NVIDIA Canary-Qwen-2.5B with speaker diarization."
)
console = Console()


@app.command()
def check_gpu():
    """Diagnoses GPU readiness, VRAM, and environment compatibility."""
    console.print(Panel.fit("[bold green]NVIDIA Canary-Qwen Diagnostic[/bold green]", border_style="green"))
    
    cuda_avail = torch.cuda.is_available()
    vram_mgr = VRAMManager()
    stats = vram_mgr.get_stats()

    table = Table(title="System & GPU Diagnostics", show_header=True, header_style="bold cyan")
    table.add_column("Property", style="dim")
    table.add_column("Value")

    table.add_row("CUDA Available", "[green]Yes[/green]" if cuda_avail else "[red]No[/red]")
    if cuda_avail:
        table.add_row("Device Name", f"[bold]{stats['device_name']}[/bold]")
        table.add_row("Total VRAM", f"{stats['total_gb']} GB")
        table.add_row("Reserved VRAM", f"{stats['reserved_gb']} GB")
        table.add_row("Free VRAM", f"{stats['free_gb']} GB")
        table.add_row("BF16 Supported", "[green]Yes[/green]" if torch.cuda.is_bf16_supported() else "[yellow]No[/yellow]")
        table.add_row("CUDA Version", torch.version.cuda or "N/A")
    else:
        table.add_row("Status", "[red]Running in CPU-only mode (GPU not detected)[/red]")

    console.print(table)


@app.command()
def transcribe(
    audio_file: Path = typer.Argument(..., help="Path to input AAC or audio file"),
    output: Optional[Path] = typer.Option(None, "--output", "-o", help="Output text file path"),
    diarizer: str = typer.Option("nemo", "--diarizer", "-d", help="Diarization route ('nemo' or 'pyannote')"),
    speaker_labels: bool = typer.Option(True, "--speaker-labels/--no-speaker-labels", help="Enable speaker diarization"),
    enhancer: bool = typer.Option(True, "--enhancer/--no-enhancer", help="Enable GPU speech noise filter & vocal amplifier"),
    ambiguity_resolver: bool = typer.Option(True, "--ambiguity-resolver/--no-ambiguity-resolver", help="Enable auto-slowdown for ambiguous audio frames"),
    council: bool = typer.Option(True, "--council/--no-council", help="Enable Multi-Model Inference Council (Canary + Whisper + Conformer)"),
    council_mode: str = typer.Option("sequential", "--council-mode", help="Council mode: 'sequential' (3-pass deep) or 'concurrent' (1-pass fast)"),
    hf_token: Optional[str] = typer.Option(None, "--hf-token", help="Hugging Face token (required if using pyannote)"),
    timestamps: bool = typer.Option(True, "--timestamps/--no-timestamps", help="Include timestamps in output")
):
    """Transcribes an audio file with speaker diarization, GPU speech enhancement, and Multi-Model Council."""
    if not audio_file.exists():
        console.print(f"[bold red]Error:[/bold red] Audio file not found: {audio_file}")
        raise typer.Exit(1)

    console.print(Panel(f"[bold cyan]Transcribing:[/bold cyan] {audio_file.name}\n[dim]Diarizer: {diarizer} | Enhancer: {enhancer} | Council: {council} ({council_mode})[/dim]", border_style="cyan"))

    from .pipeline import TranscriptionPipeline
    pipeline = TranscriptionPipeline(diarizer_type=diarizer, hf_token=hf_token)

    with Progress(
        SpinnerColumn(),
        TextColumn("[bold green]{task.description}"),
        BarColumn(),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        TimeElapsedColumn(),
        console=console
    ) as progress:
        task_id = progress.add_task("Initializing...", total=100)

        def on_progress(stage: str, frac: float, current_seg):
            pct = int(frac * 100)
            progress.update(task_id, description=stage, completed=pct)

        result = pipeline.process_file(
            file_path=audio_file,
            enable_diarization=speaker_labels,
            enable_enhancer=enhancer,
            enable_ambiguity_resolver=ambiguity_resolver,
            enable_council=council,
            council_mode=council_mode,
            progress_callback=on_progress
        )

    # Display results
    console.print("\n[bold green]✓ Transcription Complete![/bold green]\n")
    
    # Save output
    out_path = output or audio_file.with_suffix(".txt")
    out_path.write_text(result["full_text"], encoding="utf-8")
    console.print(f"[dim]Saved transcript to:[/dim] [bold]{out_path}[/bold]\n")

    # Preview
    preview = "\n".join(result["full_text"].split("\n\n")[:5])
    console.print(Panel(preview, title="[bold]Preview[/bold]", border_style="dim"))


@app.command()
def process(
    directory: Path = typer.Argument(..., help="Directory containing audio files to transcribe"),
    pattern: str = typer.Option("*.aac", "--pattern", "-p", help="Glob pattern for audio files"),
    diarizer: str = typer.Option("nemo", "--diarizer", "-d", help="Diarization route ('nemo' or 'pyannote')")
):
    """Batch process a directory of audio files."""
    files = list(directory.glob(pattern))
    if not files:
        console.print(f"[yellow]No files matching '{pattern}' found in {directory}[/yellow]")
        raise typer.Exit(0)

    console.print(f"[bold]Found {len(files)} files to transcribe in {directory}[/bold]")
    for idx, f in enumerate(files, start=1):
        console.print(f"\n[cyan]({idx}/{len(files)}) Processing {f.name}...[/cyan]")
        transcribe(audio_file=f, output=None, diarizer=diarizer, speaker_labels=True, hf_token=None, timestamps=True)


@app.command()
def serve(
    host: str = typer.Option("127.0.0.1", "--host", "-h", help="Host address to bind"),
    port: int = typer.Option(8000, "--port", "-p", help="Port to listen on"),
    reload: bool = typer.Option(False, "--reload", "-r", help="Enable auto-reload on code change")
):
    """Launches the nature-themed Web UI and FastAPI server."""
    import uvicorn
    console.print(Panel.fit(
        f"[bold green]Starting Transcript Suite Web UI[/bold green]\n"
        f"Server running at: [bold cyan]http://{host}:{port}[/bold cyan]\n"
        f"[dim]Theme: Foggy Woodland (Cloudy Day Forest)[/dim]",
        border_style="green"
    ))
    try:
        uvicorn.run("transcript_suite.web.app:app", host=host, port=port, reload=reload)
    except (KeyboardInterrupt, SystemExit):
        pass
    except RuntimeError as e:
        if "event loop" in str(e).lower():
            pass
        else:
            raise
    finally:
        console.print("[dim green]Transcript Suite server stopped cleanly.[/dim green]")


@app.command(name="monitor")
def monitor(
    host: str = typer.Option("127.0.0.1", "--host", "-h", help="Server host"),
    port: int = typer.Option(8000, "--port", "-p", help="Server port"),
    interval: float = typer.Option(1.0, "--interval", "-i", help="Refresh interval in seconds")
):
    """Live terminal tracker for running transcriptions, memory telemetry, and logs."""
    import time
    import urllib.request
    import json
    from rich.live import Live
    from rich.text import Text

    base_url = f"http://{host}:{port}"
    console.print(f"[bold green]Connecting live monitor to {base_url}...[/bold green] [dim](Press Ctrl+C to exit)[/dim]\n")

    def fetch_data():
        try:
            with urllib.request.urlopen(f"{base_url}/api/telemetry/trace?interval=1", timeout=2) as r:
                trace = json.loads(r.read().decode())
            with urllib.request.urlopen(f"{base_url}/api/telemetry/logs?limit=8", timeout=2) as r:
                logs = json.loads(r.read().decode())
            return trace, logs
        except Exception:
            return None, None

    try:
        with Live(console=console, screen=False, refresh_per_second=2) as live:
            while True:
                trace, logs = fetch_data()
                if not trace:
                    live.update(Panel(
                        f"[yellow]Waiting for Transcript Suite server at {base_url}...[/yellow]\n[dim]Start server via: transcript-suite serve --port {port}[/dim]",
                        title="[bold yellow]Server Offline[/bold yellow]",
                        border_style="yellow"
                    ))
                    time.sleep(interval)
                    continue

                active = trace.get("active_task")
                curr = trace.get("current", {})

                # Main Task & Hardware Stats Table
                table = Table(show_header=False, box=None, expand=True)
                table.add_column("Key", style="bold cyan", width=18)
                table.add_column("Value")

                if active:
                    prog = int((active.get("progress") or 0.0) * 100)
                    filename = active.get("filename", "Unknown")
                    stage = active.get("stage", "Processing")
                    table.add_row("Active Task:", f"[bold white]{filename}[/bold white] [dim]({active.get('id', '')[:8]}...)[/dim]")
                    table.add_row("Pipeline Stage:", f"[bold yellow]{stage}[/bold yellow] ({prog}%)")
                    filled = int(prog / 5)
                    bar = "█" * filled + "░" * (20 - filled)
                    table.add_row("Stage Progress:", f"[bold green]{bar}[/bold green] {prog}%")
                else:
                    table.add_row("Pipeline Status:", "[dim green]● System Idle (No active transcription running)[/dim green]")

                app_ram = curr.get("proc_ram_used_gb", 0.0)
                sys_ram = curr.get("sys_ram_used_gb", 0.0)
                sys_total = curr.get("sys_ram_total_gb", 15.3)
                sys_pct = curr.get("sys_ram_percent", 0.0)
                vram_alloc = curr.get("allocated_gb", 0.0)
                vram_res = curr.get("reserved_gb", 0.0)
                vram_total = curr.get("total_gb", 7.6)

                table.add_row("App RAM (RSS):", f"[bold green]{app_ram:.2f} GB[/bold green]")
                table.add_row("System RAM:", f"{sys_ram:.1f} / {sys_total:.1f} GB ({sys_pct}%)")
                table.add_row("GPU VRAM:", f"[bold cyan]{vram_alloc:.2f}G[/bold cyan] ({vram_res:.1f}G reserved) / {vram_total:.1f} GB")

                # Recent Logs Table
                log_table = Table(title="Live Execution Logs", show_header=True, header_style="bold dim", box=None, expand=True)
                log_table.add_column("Time", width=10, style="dim")
                log_table.add_column("Level", width=8)
                log_table.add_column("Message")

                if logs and "logs" in logs:
                    for entry in logs["logs"][-6:]:
                        lvl = entry.get("level", "INFO")
                        lvl_style = "green" if lvl == "STAGE" else "cyan" if lvl == "CHUNK" else "yellow" if lvl == "WARN" else "red" if lvl == "ERROR" else "white"
                        log_table.add_row(entry.get("time_str", "--"), f"[{lvl_style}]{lvl}[/{lvl_style}]", entry.get("message", ""))

                panel = Panel(
                    Group(table, Text(""), log_table),
                    title="[bold green]Transcript Suite - Live Terminal Monitor[/bold green]",
                    border_style="green"
                )
                live.update(panel)
                time.sleep(interval)
    except KeyboardInterrupt:
        console.print("\n[dim]Monitor stopped.[/dim]")


@app.command(name="track")
def track(
    host: str = typer.Option("127.0.0.1", "--host", "-h", help="Server host"),
    port: int = typer.Option(8000, "--port", "-p", help="Server port"),
    interval: float = typer.Option(1.0, "--interval", "-i", help="Refresh interval in seconds")
):
    """Alias for monitor: live terminal tracker for transcriptions."""
    monitor(host=host, port=port, interval=interval)


if __name__ == "__main__":
    app()
