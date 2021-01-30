#!/usr/bin/env python3
import os
import re
import sys
import shutil
import logging
import datetime
import tempfile
from typing import IO, cast, List, Tuple, BinaryIO, Optional
from pathlib import Path
from zipfile import ZipFile, BadZipFile, is_zipfile, ZIP_DEFLATED

import aiohttp
import discord
from dotenv import load_dotenv
from discord.ext import commands

# name of the role able to execute the command
ADMIN_ROLE = 'Blue Shirt'

# prefix of team channels
TEAM_PREFIX = 'team-'

# a channel to upload files that are available to all teams
COMMON_CHANNEL = 'general'

# the command options for animation file handling
ANIMATION_OPTIONS = {
    'none': None,
    'team': True,
    'separate': False,
}

logger = logging.getLogger('logs_uploader')
logger.setLevel(logging.INFO)
handler = logging.StreamHandler(sys.stdout)
handler.setLevel(logging.INFO)
logger.addHandler(handler)


# Don't post to team channels and force the guild used so testing can you DMs
DISCORD_TESTING = bool(os.getenv('DISCORD_TESTING'))
# Just post all messages to calling channel, allow DMs
DISCORD_DEBUG = bool(os.getenv('DISCORD_DEBUG'))
if DISCORD_TESTING or DISCORD_DEBUG:
    # Allow DMs in testing
    guild_only = commands.check_any(commands.guild_only(), commands.dm_only())  # type: ignore
    # print all debug messages
    logger.setLevel(logging.DEBUG)
    handler.setLevel(logging.DEBUG)
else:
    guild_only = commands.guild_only()


bot = commands.Bot(command_prefix='!')


async def log_and_reply(ctx: commands.Context, error_str: str) -> None:
    logger.error(error_str)
    await ctx.reply(error_str)


async def get_channel(
    ctx: commands.Context,
    channel_name: str,
) -> Optional[discord.TextChannel]:
    channel_name = channel_name.lower()  # all text/voice channels are lowercase
    guild = ctx.guild
    if DISCORD_DEBUG:
        # Always return calling channel
        return cast(discord.TextChannel, ctx.channel)
    if DISCORD_TESTING:
        guild_id = os.getenv('DISCORD_GUILD')
        if guild_id is None:
            guild = None
        else:
            guild = bot.get_guild(int(guild_id))

    # get team's channel by name
    if guild is None:
        raise commands.NoPrivateMessage
    channel = discord.utils.get(
        guild.channels,
        name=channel_name,
    )

    if not channel:
        await log_and_reply(
            ctx,
            f"# Channel {channel_name} not found, unable to send message",
        )
        return None
    elif not isinstance(channel, discord.TextChannel):
        await log_and_reply(
            ctx,
            f"# {channel.name} is not a text channel, unable to send message",
        )
        return None

    return channel


async def get_team_channel(
    ctx: commands.Context,
    archive_name: str,
    zip_name: str,
) -> Tuple[str, Optional[discord.TextChannel]]:
    # extract team name from filename
    tla_search = re.match(TEAM_PREFIX + r'(.*?)[-.]', archive_name)
    if not isinstance(tla_search, re.Match):
        await log_and_reply(
            ctx,
            f"# Failed to extract a TLA from {archive_name} in {zip_name}",
        )
        return '', None

    tla = tla_search.group(1)
    channel = await get_channel(ctx, f"{TEAM_PREFIX}{tla}")

    return tla, channel


def pre_test_zipfile(archive_name: str, zip_name: str) -> bool:
    if not archive_name.lower().endswith('.zip'):  # skip non-zips
        logger.debug(f"{archive_name} from {zip_name} is not a ZIP, skipping")
        return False

    # skip files not starting with TEAM_PREFIX
    if not archive_name.lower().startswith(TEAM_PREFIX):
        logger.debug(
            f"{archive_name} from {zip_name} "
            f"doesn't start with {TEAM_PREFIX}, skipping",
        )
        return False
    return True


def match_animation_files(log_name: str, animation_dir: Path) -> List[Path]:
    match_num_search = re.search(r'match-([0-9]+)', log_name)
    if not isinstance(match_num_search, re.Match):
        logger.warning(f'Invalid match name: {log_name}')
        return []
    match_num = match_num_search[1]
    logger.debug(f"Fetching animation files for match {match_num}")
    match_files = animation_dir.glob(f'match-{match_num}.*')
    return [data_file for data_file in match_files if data_file.suffix != '.mp4']


def insert_match_files(archive: Path, animation_dir: Path) -> None:
    # append animations to archive
    with ZipFile(archive, 'a', compression=ZIP_DEFLATED) as zipfile:
        for log_name in zipfile.namelist():
            if not log_name.endswith('.txt'):
                continue

            for animation_file in match_animation_files(log_name, animation_dir):
                zipfile.write(animation_file.resolve(), animation_file.name)

        # add textures sub-tree
        for texture in (animation_dir / 'textures').glob('**/*'):
            zipfile.write(
                texture.resolve(),
                texture.relative_to(animation_dir),
            )


async def send_file(
    ctx: commands.Context,
    channel: discord.TextChannel,
    archive: Path,
    event_name: str,
    msg_str: str = "Here are your logs",
    logging_str: str = "Uploaded logs",
) -> bool:
    try:
        if DISCORD_TESTING:  # don't actually send message in testing
            if (archive.stat().st_size / 1000**2) > 8:
                # discord.HTTPException requires aiohttp.ClientResponse
                await log_and_reply(
                    ctx,
                    f"# {archive.name} was too large to upload at "
                    f"{archive.stat().st_size / 1000**2 :.3f} MiB",
                )
                return False
        else:
            await channel.send(
                content=f"{msg_str} from {event_name if event_name else 'today'}",
                file=discord.File(str(archive)),
            )
        logger.debug(
            f"{logging_str} from {event_name if event_name else 'today'}",
        )
    except discord.HTTPException as e:  # handle file size issues
        if e.status == 413:
            await log_and_reply(
                ctx,
                f"# {archive.name} was too large to upload at "
                f"{archive.stat().st_size / 1000**2 :.3f} MiB",
            )
            return False
        else:
            raise e
    return True


def extract_animations(zipfile: ZipFile, tmpdir: Path, fully_extract: bool) -> bool:
    animation_files = [
        name for name in zipfile.namelist()
        if name.startswith('animations')
        and name.endswith('.zip')
    ]

    if not animation_files:
        return False

    zipfile.extract(animation_files[0], path=tmpdir)

    # give the animations archive + folder if fixed name
    shutil.move(str(tmpdir / animation_files[0]), str(tmpdir / 'animations.zip'))

    if fully_extract:
        with ZipFile(tmpdir / 'animations.zip') as animation_zip:
            (tmpdir / 'animations').mkdir()
            animation_zip.extractall(tmpdir / 'animations')
            logger.debug("Extracting animations.zip")
    return True


async def logs_upload(
    ctx: commands.Context,
    file: IO[bytes],
    zip_name: str,
    event_name: str,
    team_animation: Optional[bool] = None,  # None = don't upload animations
) -> None:
    animations_found = False
    try:
        with tempfile.TemporaryDirectory() as tmpdir_name:
            tmpdir = Path(tmpdir_name)
            completed_tlas = []

            with ZipFile(file) as zipfile:
                if team_animation is not None:
                    animations_found = extract_animations(zipfile, tmpdir, team_animation)

                    if not animations_found:
                        await log_and_reply(ctx, "animations Zip file is missing")

                for archive_name in zipfile.namelist():
                    if not pre_test_zipfile(archive_name, zip_name):
                        continue

                    zipfile.extract(archive_name, path=tmpdir)

                    if not is_zipfile(tmpdir / archive_name):  # test file is a valid zip
                        await log_and_reply(
                            ctx,
                            f"# {archive_name} from {zip_name} is not a valid ZIP file",
                        )
                        # The file will be removed with the temporary directory
                        continue

                    if team_animation and animations_found:
                        insert_match_files(tmpdir / archive_name, tmpdir / 'animations')

                    # get team's channel
                    tla, channel = await get_team_channel(ctx, archive_name, zip_name)
                    if not channel:
                        continue

                    # upload to team channel with message
                    if not await send_file(
                        ctx,
                        channel,
                        tmpdir / archive_name,
                        event_name,
                        logging_str=f"Uploaded logs for {tla}",
                    ):
                        # try again without animations
                        # TODO test this clause in unit testing
                        if team_animation:
                            # extract original archive, modified version is overwritten
                            zipfile.extract(archive_name, path=tmpdir)

                            if await send_file(  # retry with original archive
                                ctx,
                                channel,
                                tmpdir / archive_name,
                                event_name,
                                logging_str=f"Uploaded only logs for {tla}",
                            ):
                                await log_and_reply(
                                    ctx,
                                    f"Only able to upload logs for {tla}, "
                                    "no animations were served",
                                )

                        continue

                    completed_tlas.append(tla)

            if team_animation is False and animations_found:
                common_channel = await get_channel(ctx, COMMON_CHANNEL)
                # upload animations.zip to common channel
                if common_channel:
                    await send_file(
                        ctx,
                        common_channel,
                        tmpdir / 'animations.zip',
                        event_name,
                        msg_str="Here are the animation files",
                        logging_str="Uploaded animations",
                    )

            await ctx.reply(
                f"Successfully uploaded logs to {len(completed_tlas)} teams: "
                f"{', '.join(completed_tlas)}",
            )
    except BadZipFile:
        await log_and_reply(ctx, f"# {zip_name} is not a valid ZIP file")


@bot.event
async def on_ready() -> None:
    logger.info(f"{bot.user} has connected to Discord!")
    if DISCORD_TESTING:
        logger.info("Bot is running in test mode")
    if DISCORD_DEBUG:
        logger.info("Bot is running in debug mode")


@bot.command(name='logs')
@guild_only
@commands.check_any(commands.has_role(ADMIN_ROLE), commands.is_owner())  # type: ignore
async def _logs_import(
    ctx: commands.Context,
    animations: str = 'none',
    event_name: str = "",
) -> None:
    """
    Send a combined logs archive to the bot for distribution to teams
    - animations: How the animation files are handled
        - none: Ignore the animations file
        - team: Insert teams' matches into their archives
        - separate: Put the animations archive in the common channel
    - event_name: Optionally set the event name used in the bot's message to teams
    """
    logger.info(f"{ctx.author} ran '{ctx.message.content}' on {ctx.guild}:{ctx.channel}")

    if animations not in ANIMATION_OPTIONS.keys():
        await ctx.send(
            f"The animations parameter can only be: {', '.join(ANIMATION_OPTIONS.keys())}",
        )
        await ctx.send_help(_logs_import)
        return

    for file in ctx.message.attachments:
        logger.debug(
            f"Files received {file.filename}: "
            f"{file.size/1024**2 :.3f}MB, {file.size/1000**2:.3f}MiB",
        )

    if (
        ctx.message.attachments
        and ctx.message.attachments[0].filename.lower().endswith('.zip')
    ):
        with tempfile.TemporaryFile(suffix='.zip') as zipfile:
            attachment = ctx.message.attachments[0]
            filename = attachment.filename

            with ctx.typing():  # provides feedback that the bot is processing
                await attachment.save(cast(BinaryIO, zipfile), seek_begin=True)

                await logs_upload(ctx, zipfile, filename, event_name)
    else:
        logger.error(
            f"ZIP file not attached to '{ctx.message.content}' from {ctx.author}",
        )
        await ctx.send("This command requires the logs archive to be attached")
        await ctx.send_help(_logs_import)  # print corresponding command help


@bot.command(name='logs_url')
@guild_only
@commands.check_any(commands.has_role(ADMIN_ROLE), commands.is_owner())  # type: ignore
async def _logs_download(
    ctx: commands.Context,
    logs_url: str,
    animations: str = 'none',
    event_name: str = "",
) -> None:
    """
    Get combined logs archive from URL for distribution to teams, avoids Discord's size limit
    - logs_url: a download link for the combined logs archive to be distributed to teams
    - animations: How the animation files are handled
        - none: Ignore the animations file
        - team: Insert teams' matches into their archives
        - separate: Put the animations archive in the common channel
    - event_name: Optionally set the event name used in the bot's message to teams
    """
    logger.info(f"{ctx.author} ran '{ctx.message.content}' on {ctx.guild}:{ctx.channel}")

    if animations not in ANIMATION_OPTIONS.keys():
        await ctx.send(
            f"The animations parameter can only be: {', '.join(ANIMATION_OPTIONS.keys())}",
        )
        await ctx.send_help(_logs_download)
        return

    with tempfile.TemporaryFile(suffix='.zip') as zipfile:
        if logs_url.endswith('.zip'):
            filename = logs_url.split("/")[-1]
        else:
            filename = f"logs_upload-{datetime.date.today()}.zip"

        with ctx.typing():  # provides feedback that the bot is processing
            # download zip, using aiohttp
            async with aiohttp.ClientSession() as session:
                resp = await session.get(logs_url)

                if resp.status >= 400:
                    logger.error(
                        f"Download from {logs_url} failed with error "
                        f"{resp.status}, {resp.reason}",
                    )
                    await ctx.reply("Zip file failed to download")
                    return

                zipfile_data = await resp.read()

                zipfile.write(zipfile_data)

            # start processing from beginning of the file
            zipfile.seek(0)

            await logs_upload(ctx, zipfile, filename, event_name)


@bot.event
async def on_command_error(ctx: commands.Context, exception: commands.CommandError) -> None:
    if isinstance(exception, commands.MissingRequiredArgument):
        logger.info(f"{ctx.author} ran '{ctx.message.content}' on {ctx.guild}:{ctx.channel}")
        logger.error(f"A required argument '{exception.param}' is missing")
        await ctx.send(f"A required argument '{exception.param}' is missing")
        await ctx.send_help(ctx.command)  # print corresponding command help
    else:
        raise exception


if __name__ == "__main__":
    load_dotenv()
    bot.run(os.getenv('DISCORD_TOKEN', ''))
