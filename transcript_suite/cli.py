"""
Command Line Interface for Transcript Suite.
"""

from pathlib import Path
from typing import Optional
import typer
from rich.console import Console
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
    hf_token: Optional[str] = typer.Option(None, "--hf-token", help="Hugging Face token (required if using pyannote)"),
    timestamps: bool = typer.Option(True, "--timestamps/--no-timestamps", help="Include timestamps in output")
):
    """Transcribes an audio file with speaker diarization using Canary-Qwen-2.5B."""
    if not audio_file.exists():
        console.print(f"[bold red]Error:[/bold red] Audio file not found: {audio_file}")
        raise typer.Exit(1)

    console.print(Panel(f"[bold cyan]Transcribing:[/bold cyan] {audio_file.name}\n[dim]Diarizer: {diarizer} | Model: {config.model_name}[/dim]", border_style="cyan"))

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
    port: int = typer.Option(8000, "--port", "-p", help="Port to listen on")
):
    """Launches the nature-themed Web UI and FastAPI server."""
    import uvicorn
    console.print(Panel.fit(
        f"[bold green]Starting Transcript Suite Web UI[/bold green]\n"
        f"Server running at: [bold cyan]http://{host}:{port}[/bold cyan]\n"
        f"[dim]Theme: Foggy Woodland (Cloudy Day Forest)[/dim]",
        border_style="green"
    ))
    uvicorn.run("transcript_suite.web.app:app", host=host, port=port, reload=False)


if __name__ == "__main__":
    app()
