import os
import sys
import discord
import discord.ext.commands as commands
import configparser
import requests
from expiringdict import ExpiringDict

config = configparser.ConfigParser()
config.read("config.ini")
discord_token = config["Discord"]["token"]
command_prefix = config["Discord"]["prefix"]
raveberry_hostname = config["Raveberry"]["hostname"]
raveberry_port = config["Raveberry"]["port"]
stream_hostname = config["Stream"]["hostname"]
stream_port = config["Stream"]["port"]
stream_username = config["Stream"]["username"]
stream_password = config["Stream"]["password"]

cast_votes = ExpiringDict(max_len=1000, max_age_seconds=60 * 60 * 24)


def displayname(song):
    artist = song["artist"]
    title = song["title"]
    if artist:
        return f"{artist} – {title}"
    return f"{title}"


class SongDoesNotExistError(Exception):
    pass


class Raveberry(commands.Bot):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.state_url = (
            f"http://{raveberry_hostname}:{raveberry_port}/ajax/musiq/state"
        )
        self.post_url = (
            f"http://{raveberry_hostname}:{raveberry_port}/ajax/musiq/request-music/"
        )
        self.control_url = (
            f"http://{raveberry_hostname}:{raveberry_port}/ajax/musiq/"
        )
        self.vote_url = f"http://{raveberry_hostname}:{raveberry_port}/ajax/musiq/vote/"
        self.stream_url = f"http://{stream_username}:{stream_password}@{stream_hostname}:{stream_port}/stream"

        try:
            state = requests.get(self.state_url).json()
        except requests.exceptions.ConnectionError:
            print(
                "Raveberry unreachable. Make sure it is running and check your config."
            )
            sys.exit(0)
        self.platform = state["defaultPlatform"]

    async def on_ready(self):
        print(f"{self.user} has connected to Discord!")

    def identify_song(self, query):
        state = requests.get(self.state_url).json()
        state = state["musiq"]

        try:
            index = int(query)
            if index == 0:
                try:
                    key = state["currentSong"]["queueKey"]
                except TypeError:
                    raise SongDoesNotExistError
            else:
                try:
                    key = state["songQueue"][index - 1]["id"]
                except IndexError:
                    raise SongDoesNotExistError
        except ValueError:
            if query.lower() in displayname(state["currentSong"]).lower():
                return state["currentSong"]["queueKey"]
            for song in state["songQueue"]:
                if query.lower() in displayname(song).lower():
                    key = song["id"]
                    break
            else:
                raise SongDoesNotExistError
        return key


class ShortHelpCommand(commands.MinimalHelpCommand):
    async def send_pages(self):
        destination = self.get_destination()
        help_text = """```
Commands:
help                        Show this message
join                        Make the bot enter your voice channel
leave                       Make the bot leave its voice channel
play/push/enqueue           Add the given song (link or search query) to the queue
skip/next/fs                Skip the current song
pause/stop                  Pause playback
resume/res                  Resume playback
queue/q                     Show the current queue
vote_up/voteup/up/+         Vote up a song (by index or name)
vote_down/votedown/down/-   Vote down a song (by index or name)
reload/refresh              Reload the audio stream
```"""
        await destination.send(help_text)


raveberry = Raveberry(command_prefix, help_command=ShortHelpCommand())

# Because the decorator requires the bot the command is added to,
# this can not be a member variable :(
@raveberry.command(aliases=["q"])
async def queue(ctx):
    self = ctx.bot
    channel = ctx.channel
    async with channel.typing():
        state = requests.get(self.state_url).json()
        state = state["musiq"]
        queue = []
        current_song = state["currentSong"]
        song_queue = state["songQueue"]

        if current_song is None and not song_queue:
            message = "Currently empty :("
        else:
            if current_song is None:
                message = f"**0. (-)** _Empty_"
            else:
                message = (
                    f"**0. ({current_song['votes']:+})** {displayname(current_song)}"
                )
            for i, song in enumerate(state["songQueue"]):
                queue.append(f"**{i+1}. ({song['votes']:+})** {displayname(song)}")
            if queue:
                message += "\n\n"
                message += "\n".join(queue)

        embed = discord.Embed(
            title="Queue",
            colour=discord.Colour(0x9198AA),
            description=message,
        )
        embed.set_thumbnail(
            url="https://raw.githubusercontent.com/raveberry/raveberry/master/core/lights/circle/raveberry.png"
        )
        await channel.send(embed=embed)


@raveberry.command(aliases=["push", "enqueue", "p"])
async def play(ctx, *, query):
    self = ctx.bot
    channel = ctx.channel
    author_id = ctx.author.id
    async with channel.typing():
        r = requests.post(
            self.post_url,
            data={"query": query, "playlist": False, "platform": self.platform},
        )

        if r.status_code == 200:
            # Songs start with 1 vote, add to dict so users can't upvote a second time
            key = r.json()["key"]
            entry = (author_id, key)
            if entry not in cast_votes:
                cast_votes[entry] = 0
            cast_votes[entry] += 1
            await ctx.message.add_reaction("👌")
        else:
            await ctx.message.add_reaction("⚠")
            await channel.send(r.text)

@raveberry.command(alises=["stop"])
async def pause(ctx):
    self = ctx.bot
    channel = ctx.channel
    author_id = ctx.author.id
    async with channel.typing():
        r = requests.post(
            f"{self.control_url}pause",
        )
        if r.status_code == 200:
            await ctx.message.add_reaction("👌")
            await refresh(ctx)
        else:
            await ctx.message.add_reaction("⚠")

@raveberry.command(alises=["res"])
async def resume(ctx):
    self = ctx.bot
    channel = ctx.channel
    author_id = ctx.author.id
    async with channel.typing():
        r = requests.post(
            f"{self.control_url}play",
        )
        if r.status_code == 200:
            await ctx.message.add_reaction("👌")
            await refresh(ctx)
        else:
            await ctx.message.add_reaction("⚠")


@raveberry.command(aliases=["next", "fs"])
async def skip(ctx):
    self = ctx.bot
    channel = ctx.channel
    author_id = ctx.author.id
    async with channel.typing():
        r = requests.post(
            f"{self.control_url}skip",
        )
        if r.status_code == 200:
            await ctx.message.add_reaction("👌")
            await refresh(ctx)
        else:
            await ctx.message.add_reaction("⚠")


async def vote(ctx, query, amount):
    self = ctx.bot
    channel = ctx.channel
    author_id = ctx.author.id
    async with channel.typing():
        try:
            key = self.identify_song(query)
        except SongDoesNotExistError:
            await ctx.message.add_reaction("⚠")
            await channel.send(f"> {ctx.message.content}\nSong does not exist")
            return

        entry = (author_id, key)
        if entry not in cast_votes:
            cast_votes[entry] = 0
        new_vote = cast_votes[entry] + amount
        if new_vote < -1 or new_vote > 1:
            await ctx.message.add_reaction("✋")
            return

        r = requests.post(self.vote_url, data={"key": key, "amount": amount})
        if r.status_code == 200:
            cast_votes[entry] = new_vote
            await ctx.message.add_reaction("👌")
        else:
            await ctx.message.add_reaction("⚠")
            await channel.send(r.text)


@raveberry.command(aliases=["voteup", "up", "+"])
async def vote_up(ctx, *, query):
    await vote(ctx, query, 1)


@raveberry.command(aliases=["votedown", "down", "-"])
async def vote_down(ctx, *, query):
    await vote(ctx, query, -1)


@raveberry.command()
async def join(ctx):
    self = ctx.bot
    if not ctx.author.voice:
        await ctx.message.add_reaction("⚠")
        await ctx.channel.send("Please enter a voice channel")
        return
    channel = ctx.author.voice.channel
    voice = discord.utils.get(self.voice_clients, guild=ctx.guild)
    if voice and voice.is_connected():
        if voice.channel == channel:
            await ctx.message.add_reaction("👌")
            return
        voice.stop()
        await voice.move_to(channel)
    else:
        voice = await channel.connect()

    path = self.stream_url
    # Quality is better with FFmpegOpusAudio
    # voice.play(discord.FFmpegPCMAudio(path))
    voice.play(discord.FFmpegPCMAudio(path))
    await ctx.message.add_reaction("👌")


@raveberry.command()
async def leave(ctx):
    self = ctx.bot
    voice = discord.utils.get(self.voice_clients, guild=ctx.guild)
    await voice.disconnect()
    await ctx.message.add_reaction("👌")


@raveberry.command(aliases=["reload"])
async def refresh(ctx):
    self = ctx.bot
    voice = discord.utils.get(self.voice_clients, guild=ctx.guild)
    if voice is None:
        await ctx.message.add_reaction("⚠")
        await ctx.channel.send("Not in a voice chat. Use 'join' first.")
    path = self.stream_url
    voice.stop()
    voice.play(discord.FFmpegPCMAudio(path))
    await ctx.message.add_reaction("👌")


raveberry.run(discord_token)
