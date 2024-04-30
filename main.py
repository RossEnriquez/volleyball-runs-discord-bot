import discord
from discord.ext import commands, tasks
import config
from config import *
import firebase_admin
from firebase_admin import credentials, firestore
import datetime
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
import re

# discord config
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.reactions = True
bot = commands.Bot(command_prefix='$', intents=intents)

# firebase config
firebase_config = config.firebase_config
cred = credentials.Certificate(firebase_config)
db_app = firebase_admin.initialize_app(cred)
db = firestore.client()
locations_ref = db.collection('locations')
messages_ref = db.collection('message_templates')
utils_ref = db.collection('utils')
users_ref = db.collection('users')
reminders_ref = db.collection('reminders')

day_emojis = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "0️⃣"]
booked_emojis = ['👍', '👎']
bot_admins = [348420855082254337, 265329123633659904, 603050407891304459]
announcement_channel = None
control_channel = None
logs_channel = None
server = None
# internal cache
last_start_msg_id = None
last_booked_msg_id = None
last_plus_one_msg_id = None
server_id = config.SERVER_ID
token = config.TOKEN


@bot.event
async def on_ready():
    global announcement_channel, control_channel, logs_channel, server
    server = await bot.fetch_guild(server_id)
    announcement_channel = await server.fetch_channel(config.ANNOUNCEMENT_CHANNEL_ID)
    control_channel = await server.fetch_channel(config.CONTROL_CHANNEL_ID)
    logs_channel = await server.fetch_channel(config.LOGS_CHANNEL_ID)

    # check if reminders need to be sent out
    reminder_no_response_start = reminders_ref.document('no_response_start').get().to_dict()
    reminder_no_response_booked = reminders_ref.document('no_response_booked').get().to_dict()
    reminder_plus_one = reminders_ref.document('plus_one').get().to_dict()
    reminder_day_before = reminders_ref.document('day_before').get().to_dict()

    run_reminder(reminder_no_response_start, send_reminder_no_response_start)
    run_reminder(reminder_no_response_booked, send_reminder_no_response_booked)
    run_reminder(reminder_plus_one, send_reminder_plus_one)
    run_reminder(reminder_day_before, send_reminder_day_before)

    print(f'We have logged in as {bot.user}')


# To send out the start message
# NOTE: when inputting days, there must be no spaces between commas/numbers
@bot.command(name='start')
async def on_start(ctx, start, days):
    if ctx.author.id not in bot_admins:
        return

    today = datetime.today()
    start_date = datetime.strptime(str(today.year) + start, '%Y%b%d')
    selected_days = days.split(',')
    # insert start date into selected days
    selected_days.insert(0, '0')
    msg_out = messages_ref.document('start').get().to_dict()['message'].replace('\\n', '\n')
    msg_out = msg_out.replace('[start_date]', start_date.strftime('%A `%b %d`'))
    day_msgs = ''
    start_emojis = []

    for i, day_str in enumerate(selected_days[:10]):
        day_offset = int(day_str)
        # find date
        date_of_day = start_date + timedelta(days=day_offset)
        day_msgs += day_emojis[i] + date_of_day.strftime(' %A `%b %d`') + '\n'
        start_emojis.append(day_emojis[i])
    start_emojis.append('❌')

    msg_out = msg_out.replace('[selected_days]', day_msgs)
    sent_msg = await announcement_channel.send(msg_out)

    # store last start message id
    global last_start_msg_id
    last_start_msg_id = sent_msg.id
    utils_ref.document('last_start_msg').update({'id': str(sent_msg.id)})

    # add reactions to last start message
    for emoji in start_emojis:
        await sent_msg.add_reaction(emoji)

    # set reminder for people who haven't responded to start message
    reminder_no_response_datetime = datetime.utcnow() + timedelta(hours=24)
    no_response_doc = reminders_ref.document('no_response_start')
    no_response_doc.update({
        'scheduled_datetime': reminder_no_response_datetime,
        'should_reply': True
    })
    send_reminder_no_response_start.change_interval(time=reminder_no_response_datetime.time())
    send_reminder_no_response_start.start()


# To send out the booked message
@bot.command(name='booked')
async def on_booked(ctx, loc, date, time):
    if ctx.author.id not in bot_admins:
        return

    # turn off reminder for start message
    reminders_ref.document('no_response_start').update({'should_reply': False})
    send_reminder_no_response_start.cancel()

    today = datetime.today()
    location = locations_ref.document(loc).get().to_dict()
    booked_date = datetime.strptime(str(today.year) + date, '%Y%b%d')

    reply_msg = f'BOOKED A RUN @everyone\n- 🏐 {location["name"]}\n- 📍 {location["address"]}\n' \
                f'- 🗓️️ {booked_date.strftime("%A `%b %d`")} from `{time}`\n\n' + \
                messages_ref.document('booked').get().to_dict()['message'].replace('\\n', '\n')

    global last_start_msg_id, last_booked_msg_id
    # get last start message id
    if last_start_msg_id is None:
        last_start_msg_id = int(utils_ref.document('last_start_msg').get().to_dict()['id'])

    last_voting_msg = await announcement_channel.fetch_message(last_start_msg_id)
    sent_msg = await last_voting_msg.reply(reply_msg)

    # store last booked message id
    last_booked_msg_id = sent_msg.id
    utils_ref.document('last_booked_msg').update({'id': str(sent_msg.id)})

    # add reactions to last booked message
    for emoji in booked_emojis:
        await sent_msg.add_reaction(emoji)

    # create scheduled event
    event_time = time.split('-')
    start_time = datetime.strptime(event_time[0], '%I%p')
    end_time = datetime.strptime(event_time[1], '%I%p')

    start_datetime = booked_date.replace(hour=start_time.hour, tzinfo=ZoneInfo('America/Toronto'))
    end_datetime = booked_date.replace(hour=end_time.hour, tzinfo=ZoneInfo('America/Toronto'))

    await server.create_scheduled_event(
        name='🏐 Volleyball Runs 🏐',
        description='Come to our volleyball runs 😆',
        start_time=start_datetime,
        end_time=end_datetime,
        privacy_level=discord.PrivacyLevel.guild_only,
        entity_type=discord.EntityType.external,
        location=(location['name'] + ', ' + location['address']),
        reason='Booked a new volleyball run'
    )

    # set reminder for people who haven't responded to booked message
    reminder_no_response_datetime = datetime.utcnow() + timedelta(hours=12)
    plus_one_doc = reminders_ref.document('no_response_booked')
    plus_one_doc.update({
        'scheduled_datetime': reminder_no_response_datetime,
        'should_reply': True
    })
    send_reminder_no_response_booked.change_interval(time=reminder_no_response_datetime.time())
    send_reminder_no_response_booked.start()

    # set reminder for plus ones
    reminder_plus_one_datetime = datetime.utcnow() + timedelta(hours=24)
    plus_one_doc = reminders_ref.document('plus_one')
    plus_one_doc.update({
        'scheduled_datetime': reminder_plus_one_datetime,
        'should_reply': True
    })
    send_reminder_plus_one.change_interval(time=reminder_plus_one_datetime.time())
    send_reminder_plus_one.start()

    # set reminder for the day before
    reminder_day_before_datetime = booked_date.astimezone(tz=timezone.utc)
    day_before_doc = reminders_ref.document('day_before')
    day_before_doc.update({
        'scheduled_datetime': reminder_day_before_datetime,
        'should_reply': True
    })
    run_reminder(day_before_doc.get().to_dict(), send_reminder_day_before)


# To send out the pay message
# ex. $pay 11.97 "1 @user1 @user2..." "2 @user3 @user4..."
@bot.command(name='pay')
async def on_pay(ctx, price, *args):
    if ctx.author.id not in bot_admins:
        return

    server_users = set()
    nickname_to_id = {}
    async for user in server.fetch_members():
        if user.bot:
            continue
        server_users.add(user)
        nickname_to_id[user.nick] = str(user.id)

    users_attended = set()
    pay_message = f'Thanks for coming out! Can I please get an ET of **${price}** to `esantiago@rogers.com` 🙏\n'
    for user_list in args:
        users = user_list.split()
        pay_count = int(users[0])
        users = users[1:]
        pay_message += '\n'
        for user in users:
            if not user:
                continue

            # update stats
            user_nickname = re.search('@(\\w+)', user).group(1)
            # numeric match: user was mentioned and user_id was given
            if user_nickname.isnumeric():
                user_id = user_nickname
            else:
                user_id = nickname_to_id[user_nickname]
            if user_id is None:
                continue

            users_attended.add(user_id)
            user_doc = users_ref.document(user_id)
            if not user_doc.get().exists:
                add_user_to_db(user)
            user_info = user_doc.get().to_dict()

            # increase streak + total for those who went
            user_doc.update({'streak': user_info['streak'] + 1,
                             'total_times_came': user_info['total_times_came'] + 1})

            pay_message += f'<@{user_id}> '
            if pay_count > 1:
                pay_message += f'x{pay_count} '
        pay_message += '\n'

    # reset streak back to 0 for those who didn't go
    for user in server_users:
        user_id = str(user.id)
        if user_id not in users_attended:
            user_doc = users_ref.document(user_id)
            if not user_doc.get().exists:
                add_user_to_db(user)

            user_doc.update({'streak': 0})

    await announcement_channel.send(pay_message)

    # log for important notes (flops + who didn't react)
    global last_booked_msg_id, last_start_msg_id
    if last_booked_msg_id is None:
        last_booked_msg_id = int(utils_ref.document('last_booked_msg').get().to_dict()['id'])
    last_booked_msg = await announcement_channel.fetch_message(last_booked_msg_id)

    if last_start_msg_id is None:
        last_start_msg_id = int(utils_ref.document('last_start_msg').get().to_dict()['id'])
    last_start_msg = await announcement_channel.fetch_message(last_start_msg_id)

    time_now = datetime.now().strftime("%H:%M:%S")
    flops = set()
    reacted_ids = set()
    no_reaction_nicks = set()

    # collect flops
    for reaction in last_booked_msg.reactions:
        if reaction.emoji != '👍':
            continue
        async for user in reaction.users():
            if not user.bot and str(user.id) not in users_attended:
                flops.add(user)

    # collect nicknames of who didn't react
    for reaction in last_start_msg.reactions:
        async for user in reaction.users():
            reacted_ids.add(user.id)
    for reaction in last_booked_msg.reactions:
        async for user in reaction.users():
            reacted_ids.add(user.id)
    for user in server_users:
        user_id = user.id
        if user_id not in reacted_ids:
            no_reaction_nicks.add(user.nick)

    flops_msg = ''
    for flop in flops:
        flops_msg += f'{flop.nick} '
        # update flops stats
        user_doc = users_ref.document(str(flop.id))
        if not user_doc.get().exists:
            add_user_to_db(user)
        user_info = user_doc.get().to_dict()
        user_doc.update({'flops': user_info['flops'] + 1})

    await logs_channel.send(
        f'```[INFO][{time_now}] Payment message sent. Important notes:\n\n'
        f'Flops: {flops_msg}\n\nNo reaction: {no_reaction_nicks}```')


# To check the streaks leaderboard
@bot.command(name='streaks')
async def check_streaks(ctx):
    users_dict = {}
    users = users_ref.stream()
    for user in users:
        user_info = user.to_dict()
        users_dict[user_info['nickname']] = user_info['streak']

    embed_config = {
        'title': '🔥 STREAKS 🔥',
        'type': 'rich',
        'description': '**Check to see who has the longest streak of going to the 🏐 runs!**\n\n',
        'colour': discord.Colour.orange(),
        'footer': 'Tip: Make sure to react to the BOOKED messages to participate!'
    }

    await sort_then_message(ctx, users_dict, embed_config, 5)


# To check the total leaderboard
@bot.command(name='leaderboard')
async def on_leaderboard(ctx):
    users_dict = {}
    users = users_ref.stream()
    for user in users:
        user_info = user.to_dict()
        users_dict[user_info['nickname']] = user_info['total_times_came']

    embed_config = {
        'title': '🏆 LEADERBOARDS 🏆',
        'type': 'rich',
        'description': '**Check to see who has been to the 🏐 runs the most!**\n\n',
        'colour': discord.Colour.gold(),
        'footer': 'Tip: Make sure to react to the BOOKED messages to participate!'
    }

    await sort_then_message(ctx, users_dict, embed_config, 10)


@bot.command(name='flops')
async def on_flops(ctx):
    users_dict = {}
    users = users_ref.stream()
    for user in users:
        user_info = user.to_dict()
        users_dict[user_info['nickname']] = user_info['flops']

    embed_config = {
        'title': '😔 FLOP COUNTS 😔',
        'type': 'rich',
        'description': '**Check to see who has flopped on the 🏐 runs the most**\n\n',
        'colour': discord.Colour.fuchsia(),
        'footer': "NOTE: Please don't flop :("
    }

    await sort_then_message(ctx, users_dict, embed_config, 5)


# Sorts the given user list by dict value then sends an embed to the ctx channel
async def sort_then_message(ctx, users, embed_config, top_user_count):
    if len(users) == 0:
        msg = 'No users recorded yet!'
        embed_config['description'] = msg
        embed = discord.Embed(
            title=embed_config['title'],
            type=embed_config['type'],
            description=embed_config['description'],
            colour=embed_config['colour']
        )
        embed.set_footer(text=embed_config['footer'])
        embed.add_field(name='', value='', inline=False)
        await ctx.channel.send(embed=embed)
        return

    sorted_users = dict(sorted(users.items(), key=lambda item: item[1])[::-1])
    for i, user in enumerate(sorted_users):
        emoji = ''
        if i == 0:
            emoji = '🥇'
        elif i == 1:
            emoji = '🥈'
        elif i == 2:
            emoji = '🥉'
        elif i >= top_user_count and user != ctx.author.display_name:
            continue

        name_with_streak = f'{user} ({users[user]})'
        if user == ctx.author.display_name:
            name_with_streak = '**' + name_with_streak + '** ◄'
        embed_msg = f'{i + 1}. {emoji} {name_with_streak}'

        if i >= top_user_count:
            embed_msg = '---------------\n' + embed_msg
        embed_config['description'] += embed_msg + '\n'

    embed = discord.Embed(
        title=embed_config['title'],
        type=embed_config['type'],
        description=embed_config['description'],
        colour=embed_config['colour'],
    )
    embed.set_footer(text=embed_config['footer'])
    embed.add_field(name='', value='', inline=False)
    await ctx.channel.send(embed=embed)


# To remind users who haven't reacted on the start message yet
@bot.command(name='remindstart')
async def on_remind_start(ctx):
    if ctx.author.id not in bot_admins:
        return
    await remind_start()

    reminders_ref.document('no_response_start').update({'should_reply': False})
    send_reminder_no_response_start.cancel()


# To remind users who haven't reacted on the booked message and didn't react with a ❌ on the start message
@bot.command(name='remindbooked')
async def on_remind_booked(ctx):
    if ctx.author.id not in bot_admins:
        return
    await remind_booked()

    reminders_ref.document('no_response_booked').update({'should_reply': False})
    send_reminder_no_response_booked.cancel()


# To remind users who liked the booked message to react if they have a plus one
@bot.command(name='remindplusone')
async def on_remind_plus_one(ctx):
    if ctx.author.id not in bot_admins:
        return
    await remind_plus_one()

    reminders_ref.document('plus_one').update({'should_reply': False})
    send_reminder_plus_one.cancel()


# To remind users the day before of the run
@bot.command(name='reminddaybefore')
async def on_remind_day_before(ctx):
    if ctx.author.id not in bot_admins:
        return
    await remind_day_before()

    reminders_ref.document('day_before').update({'should_reply': False})
    send_reminder_day_before.cancel()


@tasks.loop(count=1)
async def send_reminder_no_response_start():
    no_response_doc = reminders_ref.document('no_response_start')
    no_response_doc.update({'should_reply': False})
    await remind_start()


@tasks.loop(count=1)
async def send_reminder_no_response_booked():
    no_response_doc = reminders_ref.document('no_response_booked')
    no_response_doc.update({'should_reply': False})
    await remind_booked()


@tasks.loop(count=1)
async def send_reminder_plus_one():
    plus_one_doc = reminders_ref.document('plus_one')
    plus_one_doc.update({'should_reply': False})
    await remind_plus_one()


@tasks.loop(count=1)
async def send_reminder_day_before():
    day_before_doc = reminders_ref.document('day_before')
    day_before_doc.update({'should_reply': False})
    await remind_day_before()


@bot.event
async def on_raw_reaction_add(payload):
    channel = server.get_channel(payload.channel_id) or await server.fetch_channel(payload.channel_id)
    user = server.get_member(payload.user_id) or await server.fetch_member(payload.user_id)
    message = await channel.fetch_message(payload.message_id)
    emoji = str(payload.emoji)

    if channel.id != announcement_channel.id or user.bot:
        return

    global last_booked_msg_id, last_start_msg_id, last_plus_one_msg_id
    if last_booked_msg_id is None:
        last_booked_msg_id = int(utils_ref.document('last_booked_msg').get().to_dict()['id'])
    if last_start_msg_id is None:
        last_start_msg_id = int(utils_ref.document('last_start_msg').get().to_dict()['id'])
    if last_plus_one_msg_id is None:
        last_plus_one_msg_id = int(utils_ref.document('last_plus_one_msg').get().to_dict()['id'])

    time_now = datetime.now().strftime("%H:%M:%S")
    if message.id == last_booked_msg_id:
        if emoji == '👍':
            await logs_channel.send(
                f'```[INFO][{time_now}] {user.nick} LIKED 👍 the last booked message```')

        elif emoji == '👎':
            await logs_channel.send(
                f'```[INFO][{time_now}] {user.nick} DISLIKED 👎 the last booked message```')

    elif message.id == last_start_msg_id:
        if emoji == '❌':
            return

        vote_limit = 12
        for reaction in message.reactions:
            if reaction.emoji != emoji or reaction.count != vote_limit + 1:
                continue

            # 12 people voted on a day - send a notif
            matched_day = re.search(f'{emoji}(.*)\n', message.content).group(1)
            await logs_channel.send(
                f'@everyone ‼️\n```[INFO][{time_now}] Day {emoji} {matched_day} reached {vote_limit} votes!```')
            return

    elif message.id == last_plus_one_msg_id:
        if emoji == '☝️':
            await logs_channel.send(f'```[INFO][{time_now}] {user.nick} has a PLUS ONE ☝️ for the run```')

        elif emoji == '✌️':
            await logs_channel.send(f'```[INFO][{time_now}] {user.nick} has PLUS TWO ️✌ for the run```')


@bot.event
async def on_raw_reaction_remove(payload):
    channel = server.get_channel(payload.channel_id) or await server.fetch_channel(payload.channel_id)
    user = server.get_member(payload.user_id) or await server.fetch_member(payload.user_id)
    message = await channel.fetch_message(payload.message_id)
    emoji = str(payload.emoji)

    if channel.id != announcement_channel.id or user.bot:
        return

    global last_booked_msg_id, last_plus_one_msg_id
    if last_booked_msg_id is None:
        last_booked_msg_id = int(utils_ref.document('last_booked_msg').get().to_dict()['id'])
    if last_plus_one_msg_id is None:
        last_plus_one_msg_id = int(utils_ref.document('last_plus_one_msg').get().to_dict()['id'])

    time_now = datetime.now().strftime("%H:%M:%S")
    if message.id == last_booked_msg_id:
        if emoji == '👍':
            await logs_channel.send(
                f'```[INFO][{time_now}] {user.nick} REMOVED A LIKE 👍 from the last booked message```')

        elif emoji == '👎':
            await logs_channel.send(
                f'```[INFO][{time_now}] {user.nick} REMOVED A DISLIKE 👎 from the last booked message```')

    elif message.id == last_plus_one_msg_id:
        if emoji == '☝️':
            await logs_channel.send(
                f'```[INFO][{time_now}] {user.nick} REMOVED A PLUS ONE ☝️ from the last plus one message```')

        elif emoji == '✌️':
            await logs_channel.send(
                f'```[INFO][{time_now}] {user.nick} REMOVED PLUS TWO ✌️️ from the last plus one message```')


@bot.event
async def on_member_join(member):
    add_user_to_db(member)


# Adds the given user to firebase/users
def add_user_to_db(user):
    if user.discriminator != '0':
        discriminator = '#' + user.discriminator
    else:
        discriminator = ''

    user_doc = users_ref.document(str(user.id))
    user_doc.set({
        'username': user.name + discriminator,
        'nickname': user.nick,
        'streak': 0,
        'total_times_came': 0,
        'flops': 0
    })


async def remind_start():
    reacted = set()
    global last_start_msg_id
    if last_start_msg_id is None:
        last_start_msg_id = int(utils_ref.document('last_start_msg').get().to_dict()['id'])
    last_start_msg = await announcement_channel.fetch_message(last_start_msg_id)

    # collect users who have reacted
    for reaction in last_start_msg.reactions:
        async for user in reaction.users():
            if not user.bot:
                reacted.add(user.id)

    # collect users who have not reacted
    not_reacted_msg = ''
    async for user in server.fetch_members():
        if not user.bot and user.id not in reacted:
            not_reacted_msg += f'<@{user.id}> '

    # everybody reacted
    time_now = datetime.now().strftime("%H:%M:%S")
    if not not_reacted_msg:
        await logs_channel.send(
            f'```[INFO][{time_now}] Reminder for booked message tried to send, but everybody reacted accordingly!```')
        return

    msg = '🔔 Reminder to react on a day!\n\n' + not_reacted_msg
    await last_start_msg.reply(msg)


async def remind_booked():
    reacted = set()
    reacted_x = set()
    global last_booked_msg_id, last_start_msg_id
    if last_booked_msg_id is None:
        last_booked_msg_id = int(utils_ref.document('last_booked_msg').get().to_dict()['id'])
    last_booked_msg = await announcement_channel.fetch_message(last_booked_msg_id)

    if last_start_msg_id is None:
        last_start_msg_id = int(utils_ref.document('last_start_msg').get().to_dict()['id'])
    last_start_msg = await announcement_channel.fetch_message(last_start_msg_id)

    # collect users who have reacted to last booked message
    for reaction in last_booked_msg.reactions:
        async for user in reaction.users():
            reacted.add(user.id)

    # collect users who have reacted ❌ to last start message
    for reaction in last_start_msg.reactions:
        if reaction.emoji != '❌':
            continue
        async for user in reaction.users():
            reacted_x.add(user.id)

    # collect users who have not reacted
    not_reacted_msg = ''
    async for user in server.fetch_members():
        if not user.bot and user.id not in reacted and user.id not in reacted_x:
            not_reacted_msg += f'<@{user.id}> '

    # everybody reacted
    time_now = datetime.now().strftime("%H:%M:%S")
    if not not_reacted_msg:
        await logs_channel.send(
            f'```[INFO][{time_now}] Reminder for booked message tried to send, but everybody reacted accordingly!```')
        return

    msg = '🔔 Reminder to react on whether or not you are coming!\n\n' + not_reacted_msg
    await last_booked_msg.reply(msg)


async def remind_plus_one():
    reacted = set()
    global last_booked_msg_id, last_plus_one_msg_id
    if last_booked_msg_id is None:
        last_booked_msg_id = int(utils_ref.document('last_booked_msg').get().to_dict()['id'])
    last_booked_msg = await announcement_channel.fetch_message(last_booked_msg_id)

    # collect users who liked the last booked message
    for reaction in last_booked_msg.reactions:
        if reaction.emoji != '👍':
            continue
        async for user in reaction.users():
            if not user.bot:
                reacted.add(user.id)

    # nobody liked the message :(
    time_now = datetime.now().strftime("%H:%M:%S")
    if not reacted:
        await logs_channel.send(
            f'```[INFO][{time_now}] Reminder for plus ones tried to send, but nobody reacted 👍 to the'
            f' last booked message :(```')
        return

    reacted_msg = '‼️ React to this message with a ☝/✌️️ if you have a +1/+2\n\n'
    for user_id in reacted:
        reacted_msg += f'<@{user_id}> '

    sent_msg = await last_booked_msg.reply(reacted_msg)
    await sent_msg.add_reaction('☝️')
    await sent_msg.add_reaction('✌️')

    last_plus_one_msg_id = sent_msg.id
    utils_ref.document('last_plus_one_msg').update({'id': str(sent_msg.id)})


async def remind_day_before():
    reacted_going = set()
    reacted_unsure = set()
    reacted_plus_one = set()
    reacted_plus_two = set()
    global last_booked_msg_id, last_plus_one_msg_id
    if last_booked_msg_id is None:
        last_booked_msg_id = int(utils_ref.document('last_booked_msg').get().to_dict()['id'])
    last_booked_msg = await announcement_channel.fetch_message(last_booked_msg_id)

    if last_plus_one_msg_id is None:
        last_plus_one_msg_id = int(utils_ref.document('last_plus_one_msg').get().to_dict()['id'])
    last_plus_one_msg = await announcement_channel.fetch_message(last_plus_one_msg_id)

    # collect users who liked the last booked message
    for reaction in last_booked_msg.reactions:
        if reaction.emoji != '👍':
            continue
        async for user in reaction.users():
            if not user.bot:
                reacted_going.add(user.id)

    # collect users who have reacted +1/+2 to last plus one message
    for reaction in last_plus_one_msg.reactions:
        if reaction.emoji == '☝️':
            async for user in reaction.users():
                if not user.bot:
                    reacted_plus_one.add(user.id)

        elif reaction.emoji == '✌️':
            async for user in reaction.users():
                if not user.bot:
                    reacted_plus_two.add(user.id)

    event_info = re.search('@everyone\n((\n|.)*)React 👍', last_booked_msg.content).group(1)
    msg = f'🏐 Just a reminder that we are playing tomorrow at:\n{event_info}'
    if reacted_going:
        msg += f'Going ({len(reacted_going)}):\n'
        for user_id in reacted_going:
            msg += f'<@{user_id}> '
        msg += '\n\n'

    if reacted_plus_one:
        msg += f'With plus ones (+{len(reacted_plus_one)}):\n'
        for user_id in reacted_plus_one:
            msg += f'<@{user_id}> '
        msg += '\n\n'

    if reacted_plus_two:
        msg += f'With plus twos (+{2 * len(reacted_plus_two)}):\n'
        for user_id in reacted_plus_two:
            msg += f'<@{user_id}> '
        msg += '\n\n'

    if reacted_unsure:
        msg += 'Please like or dislike ASAP:\n'
        for user_id in reacted_unsure:
            msg += f'<@{user_id}> '
        msg += '\n\n'

    await last_booked_msg.reply(msg)


# Checks if the reminder should be sent out and if so, schedules a task
def run_reminder(reminder, do_reminder):
    if not reminder['should_reply']:
        return

    scheduled_datetime = datetime.utcfromtimestamp(reminder['scheduled_datetime'].timestamp())

    # only start reminder if it's within 24h of the scheduled datetime
    if (scheduled_datetime - datetime.utcnow()).days == 0:
        do_reminder.change_interval(time=scheduled_datetime.time())
        do_reminder.start()
        print(f'Reminder set for {scheduled_datetime}')


bot.run(token)
