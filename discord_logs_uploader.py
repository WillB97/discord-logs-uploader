#!/usr/bin/env python3
import os
import re
import sys
import logging
import tempfile
from typing import IO, cast, Tuple, BinaryIO, Optional
from pathlib import Path
from zipfile import ZipFile, BadZipFile, is_zipfile

import discord
from dotenv import load_dotenv
from discord.ext import commands

# name of the role able to execute the command
ADMIN_ROLE = 'Blue Shirt'

# prefix of team channels
TEAM_PREFIX = 'team-'

logger = logging.getLogger('logs_uploader')
logger.setLevel(logging.INFO)
handler = logging.StreamHandler(sys.stdout)
handler.setLevel(logging.INFO)
logger.addHandler(handler)

bot = commands.Bot(command_prefix='!')


async def log_and_reply(ctx: commands.Context, error_str: str) -> None:
    logger.error(error_str)
    await ctx.reply(error_str)


async def get_channel(
    ctx: commands.Context,
    channel_name: str,
) -> Optional[discord.TextChannel]:
    # get team's channel by name
    if ctx.guild is None:
        raise commands.NoPrivateMessage
    channel = discord.utils.get(
        ctx.guild.channels,
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


def pre_test_zipfile(
    archive_name: str,
    zipfile: ZipFile,
    zip_name: str,
) -> bool:
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


async def send_file(
    ctx: commands.Context,
    channel: discord.TextChannel,
    archive: Path,
    event_name: str,
    msg_str: str = "Here are your logs",
    logging_str: str = "Uploaded logs",
) -> bool:
    try:
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


async def logs_upload(
    ctx: commands.Context,
    file: IO[bytes],
    zip_name: str,
    event_name: str,
) -> None:
    try:
        with tempfile.TemporaryDirectory() as tmpdir_name:
            tmpdir = Path(tmpdir_name)
            completed_tlas = []

            with ZipFile(file) as zipfile:
                for archive_name in zipfile.namelist():
                    if not pre_test_zipfile(archive_name, zipfile, zip_name):
                        continue

                    zipfile.extract(archive_name, path=tmpdir)

                    if not is_zipfile(tmpdir / archive_name):  # test file is a valid zip
                        await log_and_reply(
                            ctx,
                            f"# {archive_name} from {zip_name} is not a valid ZIP file",
                        )
                        # The file will be removed with the temporary directory
                        continue

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
                        continue

                    completed_tlas.append(tla)

            await ctx.reply(
                f"Successfully uploaded logs to {len(completed_tlas)} teams: "
                f"{', '.join(completed_tlas)}",
            )
    except BadZipFile:
        await log_and_reply(ctx, f"# {zip_name} is not a valid ZIP file")


@bot.event
async def on_ready() -> None:
    logger.info(f"{bot.user} has connected to Discord!")


@bot.command()
@commands.guild_only()
@commands.check_any(commands.has_role(ADMIN_ROLE), commands.is_owner())  # type: ignore
async def logs_import(ctx: commands.Context, event_name: str = "") -> None:
    """
    Send a combined logs archive to the bot for distribution to teams
    - event_name: Optionally set the event name used in the bot's message to teams
    """
    logger.info(f"{ctx.author} ran '{ctx.message.content}' on {ctx.guild}:{ctx.channel}")

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
        await ctx.reply("This command requires the logs archive to be attached")


load_dotenv()
bot.run(os.getenv('DISCORD_TOKEN', ''))
