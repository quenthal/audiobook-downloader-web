from audiobookdl import AudiobookFile, Source, logging, Audiobook
from audiobookdl.exceptions import UserNotAuthorized, NoFilesFound, DownloadError
from . import metadata, output, encryption

import os
import shutil
import subprocess
import re
from functools import partial
from typing import Any, Iterable, List, Optional, Sequence, Tuple, Union
from rich.progress import Progress, BarColumn, ProgressColumn, SpinnerColumn
from rich.prompt import Confirm
from multiprocessing.pool import ThreadPool
from pathlib import Path
from math import log10


DOWNLOAD_PROGRESS: List[Union[str, ProgressColumn]] = [
    SpinnerColumn(),
    "{task.description}",
    BarColumn(),
    "[progress.percentage]{task.percentage:>3.0f}%"
]


def download(audiobook: Audiobook, options):
    """
    Download contents of audiobook.
    """

    try:
        template = options.output_template

        # Nextory-specific default library structure.
        # Preserve an explicitly configured custom template.
        if (
            is_nextory_audiobook(audiobook)
            and template in (None, "", "{title}")
        ):
            template = "{primary_author}/{title}"

        output_dir = output.gen_output_location(
            template,
            audiobook.metadata,
            options.remove_chars
        )

        download_audiobook(audiobook, output_dir, options)

    except KeyboardInterrupt:
        logging.book_update("Stopped download")
        logging.book_update("Cleaning up files")

        if len(audiobook.files) == 1:
            filepath, filepath_tmp = create_filepath(
                audiobook,
                output_dir,
                0
            )

            if os.path.exists(filepath_tmp):
                os.remove(filepath_tmp)

        elif os.path.isdir(output_dir):
            shutil.rmtree(output_dir)

def download_audiobook(
    audiobook: Audiobook,
    output_dir: str,
    options
):
    """
    Download, combine, remux and add metadata.
    """

    nextory = is_nextory_audiobook(audiobook)

    if nextory:
        requested_format = (
            options.output_format.lower()
            if options.output_format
            else "m4b"
        )

        if requested_format not in ("m4b", "m4a"):
            raise RuntimeError(
                "Nextory v2 supports output formats m4b and m4a. "
                "Use no -f option for M4B or use -f m4a."
            )

        if requested_format == "m4b":
            final_path = os.path.join(
                output_dir,
                f"{safe_filename(audiobook.title)}.m4b"
            )

            if options.skip_downloaded and os.path.isfile(final_path):
                logging.log(
                    f"Skipping [blue]{audiobook.title}[/], "
                    "file already exists."
                )
                return

        elif options.skip_downloaded and os.path.isdir(output_dir):
            existing_m4a = [
                name
                for name in os.listdir(output_dir)
                if name.lower().endswith(".m4a")
            ]

            if existing_m4a:
                logging.log(
                    f"Skipping [blue]{audiobook.title}[/], "
                    "chapter files already exist."
                )
                return

    else:
        if options.skip_downloaded:
            is_single_file = (
                len(audiobook.files) == 1
                or options.combine
            )

            if is_single_file and audiobook.files:
                current_format = audiobook.files[0].ext
                output_format = (
                    options.output_format
                    or current_format
                )
                output_path = f"{output_dir}.{output_format}"

                if os.path.exists(output_path):
                    logging.log(
                        f"Skipping [blue]{audiobook.title}[/], "
                        "file already exists."
                    )
                    return

            elif os.path.isdir(output_dir):
                logging.log(
                    f"Skipping [blue]{audiobook.title}[/], "
                    "directory already exists."
                )
                return

    filepaths = download_files_with_cli_output(
        audiobook,
        output_dir
    )

    if nextory:
        if requested_format == "m4b":
            logging.book_update("Creating M4B")
            final_path = create_nextory_m4b(
                audiobook,
                filepaths,
                output_dir
            )

            write_cover_file(audiobook, output_dir)
            add_metadata_to_file(
                audiobook,
                final_path,
                options
            )
            return

        logging.book_update("Creating M4A chapters")
        chapter_paths = create_nextory_m4a_chapters(
            audiobook,
            filepaths,
            output_dir
        )

        add_metadata_to_dir(
            audiobook,
            chapter_paths,
            output_dir,
            options
        )
        return

    current_format, output_format = get_output_audio_format(
        options.output_format,
        filepaths
    )

    if options.combine and len(filepaths) > 1:
        logging.book_update("Combining files")
        output_path = f"{output_dir}.{current_format}"
        output.combine_audiofiles(
            filepaths,
            output_dir,
            output_path
        )
        filepaths = [output_path]

    if current_format != output_format:
        logging.book_update("Converting files")
        filepaths = output.convert_output(
            filepaths,
            output_format
        )

    if len(filepaths) == 1:
        add_metadata_to_file(
            audiobook,
            filepaths[0],
            options
        )
    else:
        add_metadata_to_dir(
            audiobook,
            filepaths,
            output_dir,
            options
        )


def is_nextory_audiobook(audiobook: Audiobook) -> bool:
    return bool(audiobook.files) and all(
        "nextory_chapter=" in audiobook_file.url
        for audiobook_file in audiobook.files
    )


def safe_filename(value: str) -> str:
    value = re.sub(r'[\\/:*?"<>|]+', "-", value)
    value = re.sub(r"\\s+", " ", value).strip()
    return value or "Audiobook"


def get_nextory_chapter_number(audiobook_file: AudiobookFile) -> int:
    fragment = audiobook_file.url.rsplit("#", 1)[-1]

    for value in fragment.split("&"):
        if value.startswith("nextory_chapter="):
            return int(value.split("=", 1)[1])

    raise RuntimeError("Nextory chapter marker missing")


def group_nextory_segments(
    audiobook: Audiobook,
    filepaths: Sequence[str]
):
    groups = {}

    for audiobook_file, filepath in zip(
        audiobook.files,
        filepaths
    ):
        chapter_number = get_nextory_chapter_number(
            audiobook_file
        )
        groups.setdefault(chapter_number, []).append(filepath)

    return groups


def write_concat_file(
    concat_path: str,
    filepaths: Sequence[str]
):
    with open(concat_path, "w", encoding="utf-8") as concat_file:
        for filepath in filepaths:
            absolute_path = os.path.abspath(filepath)
            escaped_path = absolute_path.replace("'", "'\\\\''")
            concat_file.write(f"file '{escaped_path}'\n")



def combine_nextory_aac_segments(
    filepaths: Sequence[str],
    output_path: str,
    work_dir: str
):
    """Losslessly remux and concatenate Nextory AAC segments."""

    if not filepaths:
        raise RuntimeError("No Nextory segments to combine")

    segment_dir = os.path.join(
        work_dir,
        ".nextory-remux-segments"
    )
    os.makedirs(segment_dir, exist_ok=True)

    remuxed_paths = []

    try:
        # Remux all raw AAC segments to timestamped M4A files
        # in one FFmpeg invocation. Audio is copied, not encoded.
        command = [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
        ]

        for filepath in filepaths:
            command.extend(["-i", filepath])

        for index in range(len(filepaths)):
            remuxed_path = os.path.join(
                segment_dir,
                f"segment-{index:04d}.m4a"
            )
            remuxed_paths.append(remuxed_path)

            command.extend([
                "-map",
                f"{index}:a:0",
                "-c:a",
                "copy",
                "-movflags",
                "+faststart",
                remuxed_path,
            ])

        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
        )

        if result.returncode != 0:
            raise RuntimeError(
                "Nextory segment remux failed:\n"
                f"{result.stderr}"
            )

        # M4A segments now have usable timestamps and durations,
        # so they can be concatenated losslessly.
        run_ffmpeg_concat(
            remuxed_paths,
            output_path,
            work_dir
        )

    finally:
        if os.path.isdir(segment_dir):
            shutil.rmtree(segment_dir)


def run_ffmpeg_concat(
    filepaths: Sequence[str],
    output_path: str,
    work_dir: str
):
    concat_path = os.path.join(
        work_dir,
        ".nextory-concat.txt"
    )

    write_concat_file(concat_path, filepaths)

    try:
        result = subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-hide_banner",
                "-loglevel",
                "error",
                "-fflags",
                "+genpts",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                concat_path,
                "-map",
                "0:a:0",
                "-avoid_negative_ts",
                "make_zero",
                "-c:a",
                "copy",
                "-movflags",
                "+faststart",
                output_path,
            ],
            capture_output=True,
            text=True,
        )

        if result.returncode != 0:
            raise RuntimeError(
                f"FFmpeg concat failed for {output_path}:\\n"
                f"{result.stderr}"
            )

    finally:
        if os.path.exists(concat_path):
            os.remove(concat_path)


def remove_source_segments(filepaths: Sequence[str]):
    for filepath in filepaths:
        if os.path.isfile(filepath):
            os.remove(filepath)


def get_audio_duration_ms(filepath: str) -> int:
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            filepath,
        ],
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        raise RuntimeError(
            f"Could not determine duration of {filepath}:\n"
            f"{result.stderr}"
        )

    try:
        return round(float(result.stdout.strip()) * 1000)
    except ValueError as error:
        raise RuntimeError(
            f"Invalid duration returned for {filepath}: "
            f"{result.stdout!r}"
        ) from error


def create_nextory_m4b(
    audiobook: Audiobook,
    filepaths: Sequence[str],
    output_dir: str
) -> str:
    """
    Create one M4B without re-encoding.

    Nextory's start_at values are not assumed to be cumulative.
    Chapter start times are calculated from the actual durations
    of the chapter audio.
    """

    os.makedirs(output_dir, exist_ok=True)

    groups = group_nextory_segments(
        audiobook,
        filepaths
    )

    chapter_numbers = sorted(groups)
    temporary_chapters = []
    cumulative_start_ms = 0

    try:
        for position, chapter_number in enumerate(
            chapter_numbers,
            start=1
        ):
            logging.book_update(
                f"Preparing chapter "
                f"{position}/{len(chapter_numbers)}"
            )

            temporary_path = os.path.join(
                output_dir,
                f".nextory-chapter-{chapter_number:04d}.m4a"
            )

            combine_nextory_aac_segments(
                groups[chapter_number],
                temporary_path,
                output_dir
            )

            temporary_chapters.append(temporary_path)

            chapter_index = position - 1

            if chapter_index < len(audiobook.chapters):
                audiobook.chapters[chapter_index].start = (
                    cumulative_start_ms
                )
            else:
                raise RuntimeError(
                    "Chapter count does not match the Nextory "
                    "audio-file groups."
                )

            duration_ms = get_audio_duration_ms(
                temporary_path
            )

            cumulative_start_ms += duration_ms

        output_path = f"{output_dir}.m4b"

        logging.book_update("Combining prepared chapters")

        run_ffmpeg_concat(
            temporary_chapters,
            output_path,
            output_dir
        )

        remove_source_segments(filepaths)

        return output_path

    finally:
        for temporary_path in temporary_chapters:
            if os.path.isfile(temporary_path):
                os.remove(temporary_path)

def chapter_title(
    audiobook: Audiobook,
    chapter_number: int
) -> str:
    index = chapter_number - 1

    if index < len(audiobook.chapters):
        title = audiobook.chapters[index].title.strip()

        if title:
            return title

    return f"Luku {chapter_number}"


def chapter_filename(
    audiobook: Audiobook,
    chapter_number: int,
    total: int
) -> str:
    width = max(2, len(str(total)))
    title = chapter_title(audiobook, chapter_number)
    fallback = f"Luku {chapter_number}"

    if title == fallback:
        return f"Luku {chapter_number:0{width}d}.m4a"

    return (
        f"{chapter_number:0{width}d} - "
        f"{safe_filename(title)}.m4a"
    )


def create_nextory_m4a_chapters(
    audiobook: Audiobook,
    filepaths: Sequence[str],
    output_dir: str
) -> List[str]:
    groups = group_nextory_segments(
        audiobook,
        filepaths
    )

    chapter_paths = []
    total = len(groups)

    for chapter_number in sorted(groups):
        logging.book_update(
            f"Creating chapter "
            f"{chapter_number}/{total}"
        )

        output_path = os.path.join(
            output_dir,
            chapter_filename(
                audiobook,
                chapter_number,
                total
            )
        )

        run_ffmpeg_concat(
            groups[chapter_number],
            output_path,
            output_dir
        )

        chapter_paths.append(output_path)

    remove_source_segments(filepaths)
    return chapter_paths


def write_cover_file(
    audiobook: Audiobook,
    output_dir: str
):
    if not audiobook.cover:
        return

    cover_path = os.path.join(
        output_dir,
        f"cover.{audiobook.cover.extension}"
    )

    with open(cover_path, "wb") as cover_file:
        cover_file.write(audiobook.cover.image)

def add_metadata_to_file(audiobook: Audiobook, filepath: str, options):
    """
    Embed metadata into a single file

    :param audiobook: Audiobook object. Stores metadata
    :param filepath: Filepath of output file
    :options: Cli options
    """
    # General metadata
    logging.book_update("Adding metadata")
    metadata.add_metadata(filepath, audiobook.metadata)
    if options.write_json_metadata:
        with open(f"{filepath}.json", "w") as f:
            f.write(audiobook.metadata.as_json())
    # Chapters
    if audiobook.chapters and not options.no_chapters:
        logging.book_update("Adding chapters")
        metadata.add_chapters(filepath, audiobook.chapters)
    # Cover
    if audiobook.cover:
        logging.book_update("Embedding cover")
        metadata.embed_cover(filepath, audiobook.cover)



def add_metadata_to_dir(
    audiobook: Audiobook,
    filepaths: Iterable[str],
    output_dir: str,
    options
):
    """
    Add book and chapter metadata to separate M4A files.
    """

    filepaths = list(filepaths)
    total_tracks = len(filepaths)

    logging.book_update("Adding metadata")

    for track_number, filepath in enumerate(
        filepaths,
        start=1
    ):
        metadata.add_metadata(
            filepath,
            audiobook.metadata
        )

        title = chapter_title(
            audiobook,
            track_number
        )

        temporary_path = os.path.join(
            output_dir,
            f".tagged-{track_number:04d}.m4a"
        )

        command = [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            filepath,
            "-map",
            "0",
            "-map_metadata",
            "0",
            "-c",
            "copy",
            "-metadata",
            f"title={title}",
            "-metadata",
            f"album={audiobook.title}",
            "-metadata",
            f"track={track_number}/{total_tracks}",
            "-metadata",
            "genre=Audiobook",
        ]

        if audiobook.metadata.authors:
            authors = "; ".join(
                audiobook.metadata.authors
            )

            command.extend([
                "-metadata",
                f"artist={authors}",
                "-metadata",
                f"album_artist={authors}",
            ])

        if audiobook.metadata.narrators:
            narrators = "; ".join(
                audiobook.metadata.narrators
            )

            command.extend([
                "-metadata",
                f"narrator={narrators}",
            ])

        command.append(temporary_path)

        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
        )

        if result.returncode != 0:
            if os.path.exists(temporary_path):
                os.remove(temporary_path)

            raise RuntimeError(
                f"Metadata tagging failed for {filepath}:\\n"
                f"{result.stderr}"
            )

        os.replace(temporary_path, filepath)

        if audiobook.cover:
            metadata.embed_cover(
                filepath,
                audiobook.cover
            )

    if options.write_json_metadata:
        metadata_path = os.path.join(
            output_dir,
            "metadata.json"
        )

        with open(
            metadata_path,
            "w",
            encoding="utf-8"
        ) as metadata_file:
            metadata_file.write(
                audiobook.metadata.as_json()
            )

    write_cover_file(audiobook, output_dir)

def download_files_with_cli_output(audiobook: Audiobook, output_dir: str) -> List[str]:
    """
    Download `audiobook` with cli progress bar

    :param audiobook: Audiobook to download
    :param output_dir: Output directory where files are downloaded to
    :returns: A list of paths of the downloaded files
    """
    if len(audiobook.files) > 1:
        setup_download_dir(output_dir)
    else:
        parent = Path(output_dir).parent
        if not parent.exists():
            os.makedirs(parent)
    with logging.progress(DOWNLOAD_PROGRESS) as progress:
        task = progress.add_task(
            f"Downloading [blue]{audiobook.title}",
            total = len(audiobook.files)
        )
        update_progress = partial(progress.advance, task)
        filepaths = download_files(audiobook, output_dir, update_progress)
        # Make sure progress bar is at 100%
        remaining_progress: float = progress.tasks[0].remaining or 0
        update_progress(remaining_progress)
        # Return filenames of downloaded files
        return filepaths


def create_filepath(audiobook: Audiobook, output_dir: str, index: int) -> Tuple[str, str]:
    """
    Create output file path for file number `index` in `audibook`

    :param audiobook: Currently downloading audiobook
    :param output_dir: Directory where file should be stored
    :param index: Index in audiobooks list of files
    :returns: Filepath, Filepath_tmp
    """
    extension = audiobook.files[index].ext
    if len(audiobook.files) == 1:
        path = f"{output_dir}.{extension}"
    else:
        padded_index = str(index).zfill(int(log10(len(audiobook.files))))
        name = f"Part {padded_index}.{extension}"
        path = os.path.join(output_dir, name)
    path_tmp = f"{path}.tmp"
    return path, path_tmp


def download_file(args: Tuple[Audiobook, str, int, Any]) -> str:
    # Prepare download
    audiobook, output_dir, index, update_progress = args
    file = audiobook.files[index]
    filepath, filepath_tmp = create_filepath(audiobook, output_dir, index)
    logging.debug(f"Starting downloading file: {file.url}")
    request = audiobook.session.get(file.url, headers=file.headers, stream=True)
    content_type: Optional[str] =  request.headers.get("Content-type", None)
    if ((file.expected_content_type and file.expected_content_type != content_type) 
        or (file.expected_status_code and file.expected_status_code != request.status_code)):
        raise DownloadError(status_code=request.status_code,
                            content_type=content_type,
                            expected_status_code=file.expected_status_code,
                            expected_content_type=file.expected_content_type,
                            )
    total_filesize = int(request.headers["Content-length"])
    if not file.expected_status_code:
        logging.debug(f"expected_status_code not set by source, status-code is {request.status_code}, please update the source implementation")
    if not file.expected_content_type:
        logging.debug(f"expected_content_type not set by source, content-type is {content_type}, please update the source implementation")
    # Download file to tmp file
    with open(filepath_tmp, "wb") as f:
        for chunk in request.iter_content(chunk_size=1024):
            f.write(chunk)
            download_progress = len(chunk)/total_filesize
            update_progress(download_progress)
    # Decrypt file if necessary
    if file.encryption_method:
        encryption.decrypt_file(filepath_tmp, file.encryption_method)
    # rename file after download is complete
    os.rename(filepath_tmp, filepath)
    # Return filepath
    return filepath


def download_files(audiobook: Audiobook, output_dir: str, update_progress) -> List[str]:
    """Download files from audiobook and return paths of the downloaded files"""
    filepaths = []
    with ThreadPool(processes=20) as pool:
        arguments = []
        for index in range(len(audiobook.files)):
            arguments.append((audiobook, output_dir, index, update_progress))
        for filepath in pool.imap(download_file, arguments):
            filepaths.append(filepath)
    return filepaths


def get_output_audio_format(option: Optional[str], files: Sequence[str]) -> Tuple[str, str]:
    """
    Get output format for files

    `option` is used if specified; else it's based on the file extensions
    :param option: User specified value
    :param files: Audio file names
    :returns: A tuple with current format and output format
    """
    current_format = os.path.splitext(files[0])[1][1:]
    if option:
        output_format = option
    else:
        output_format = current_format
    return current_format, output_format


def setup_download_dir(path: str) -> None:
    """
    Creates output folder for the audiobook.
    Will give a prompt if the folder already exists.

    :param path: Path of output folder
    :returns: Nothing
    """
    logging.book_update("Creating output dir")
    if os.path.isdir(path):
        answer = Confirm.ask(
            f"The folder '[blue]{path}[/blue]' already exists. Do you want to override it?"
        )
        if answer:
            shutil.rmtree(path)
        else:
            exit()
    os.makedirs(path)
