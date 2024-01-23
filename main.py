import discord
from discord.ext import commands
import config
from config import *
import firebase_admin
from firebase_admin import credentials, firestore
import datetime
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

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

day_emojis = ["1️⃣ ", "2️⃣ ", "3️⃣ ", "4️⃣ ", "5️⃣ ", "6️⃣ ", "7️⃣ ", "8️⃣ ", "9️⃣ ", "0️⃣ "]
announcement_channel = None
# cache msg ids
last_start_msg_id = None
last_booked_msg_id = None
server_id = config.SERVER_ID
token = config.TOKEN


@bot.event
async def on_ready():
    global announcement_channel, server_id
    announcement_channel = bot.get_channel(config.ANNOUNCEMENT_CHANNEL_ID)
    server_id = config.SERVER_ID

    print(f'We have logged in as {bot.user}')


# To send out the start message
# NOTE: when inputting days, there must be no spaces between commas/numbers
@bot.command(name='start')
async def on_start(ctx, start, days):
    today = datetime.today()
    start_date = datetime.strptime(str(today.year) + start, '%Y%b%d')
    selected_days = days.split(',')
    # insert start date into selected days
    selected_days.insert(0, '0')
    msg_out = messages_ref.document('start').get().to_dict()['message'].replace('\\n', '\n')
    msg_out = msg_out.replace('[start_date]', start_date.strftime('%A `%b %d`'))
    day_msgs = ''

    for i, day_str in enumerate(selected_days[:10]):
        day_offset = int(day_str)
        # find date
        date_of_day = start_date + timedelta(days=day_offset)
        day_msgs += day_emojis[i] + date_of_day.strftime('%A `%b %d`') + '\n'

    msg_out = msg_out.replace('[selected_days]', day_msgs)
    await announcement_channel.send(msg_out)

    # store last start message id
    global last_start_msg_id
    last_start_msg_id = announcement_channel.last_message_id
    utils_ref.document('last_start_msg').update({'id': last_start_msg_id})


# To send out the booked message
@bot.command(name='booked')
async def on_booked(ctx, loc, date, time):
    today = datetime.today()
    location = locations_ref.document(loc).get().to_dict()
    booked_date = datetime.strptime(str(today.year) + date, '%Y%b%d')

    reply_msg = f'BOOKED A RUN @everyone\n- {location["name"]}\n- {location["address"]}\n' \
                f'- {booked_date.strftime("%A `%b %d`")} from `{time}`\n\n' + \
                messages_ref.document('booked').get().to_dict()['message'].replace('\\n', '\n')

    # get last start message id
    global last_start_msg_id
    if last_start_msg_id is None:
        last_start_msg_id = utils_ref.document('last_start_msg').get().to_dict()['id']

    last_voting_msg = await announcement_channel.fetch_message(last_start_msg_id)
    await last_voting_msg.reply(reply_msg)

    # store last booked message id
    global last_booked_msg_id
    last_booked_msg_id = announcement_channel.last_message_id
    utils_ref.document('last_booked_msg').update({'id': last_start_msg_id})

    event_time = time.split('-')
    start_time = datetime.strptime(event_time[0], '%I%p')
    end_time = datetime.strptime(event_time[1], '%I%p')

    start_datetime = booked_date.replace(hour=start_time.hour, tzinfo=ZoneInfo('America/Toronto'))
    end_datetime = booked_date.replace(hour=end_time.hour, tzinfo=ZoneInfo('America/Toronto'))

    await bot.get_guild(server_id).create_scheduled_event(
        name='Volleyball Runs',
        description='Come to our volleyball runs',
        start_time=start_datetime,
        end_time=end_datetime,
        privacy_level=discord.PrivacyLevel.guild_only,
        entity_type=discord.EntityType.external,
        location=(location['name'] + ', ' + location['address']),
        reason='Booked new volleyball run'
    )


# To check the streaks leaderboard
@bot.command(name='streaks')
async def check_streaks(ctx):
    users_dict = {}
    users = users_ref.stream()
    embed_config = {
        'title': '🏆 STREAKS 🏆',
        'type': 'rich',
        'description': '**Check to see who has the longest streak 🔥 of going to the runs!**\n\n',
        'colour': discord.Colour.gold(),
        'footer': 'Tip: Make sure to react to the BOOKED messages to participate!'
    }

    for user in users:
        user_info = user.to_dict()
        users_dict[user_info['username']] = user_info['streak']

    if len(users_dict) == 0:
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

    sorted_users = sorted(users_dict)[::-1]
    if len(sorted_users) > 5:
        sorted_users = sorted_users[:5]

    for i, user in enumerate(sorted_users):
        if i == 0:
            emoji = '🥇'
        elif i == 1:
            emoji = '🥈'
        elif i == 2:
            emoji = '🥉'
        else:
            emoji = ''
        embed_config['description'] += f'{i + 1}. {emoji} {user} ({users_dict[user]})\n'

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
@bot.command(name='remind')
async def on_remind(ctx):
    reacted = set()
    global last_start_msg_id, announcement_channel
    if last_start_msg_id is None:
        last_start_msg_id = utils_ref.document('last_start_msg').get().to_dict()['id']
    last_start_msg = await announcement_channel.fetch_message(last_start_msg_id)

    # collect users who have reacted
    for reaction in last_start_msg.reactions:
        async for user in reaction.users():
            reacted.add(user.id)

    # collect users who have not reacted
    not_reacted_msg = ''
    for user in bot.get_guild(server_id).members:
        if user.id not in reacted and not user.bot:
            not_reacted_msg += f'<@{user.id}> '

    # everybody reacted
    if not not_reacted_msg:
        return

    msg = '⏰ Reminder to react on a day!\n\n' + not_reacted_msg
    await last_start_msg.reply(msg)


# for testing purposes - can add whatever you want
@bot.command(name='test')
async def test(ctx):
    for user in bot.get_guild(server_id).members:
        if not user.bot:
            print(user.name)


@bot.event
async def on_reaction_add(reaction, user):
    if reaction.message.channel.id != announcement_channel.id:
        return

    global last_booked_msg_id
    if last_booked_msg_id is None:
        last_booked_msg_id = utils_ref.document('last_booked_msg').get().to_dict()['id']

    if reaction.message.id != last_booked_msg_id:
        return

    if reaction.emoji == '👍':
        user_doc = users_ref.document(str(user.id))
        if user_doc.get().exists:
            user_info = user_doc.get().to_dict()
            user_doc.update({'streak': user_info['streak'] + 1,
                             'last_streak': user_info['streak'] + 1})
        else:
            add_user_to_db(user)

    elif reaction.emoji == '👎':
        user_doc = users_ref.document(str(user.id))
        if user_doc.get().exists:
            user_doc.update({'streak': 0})
        else:
            add_user_to_db(user)


@bot.event
async def on_reaction_remove(reaction, user):
    if reaction.message.channel.id != announcement_channel.id:
        return

    global last_booked_msg_id
    if last_booked_msg_id is None:
        last_booked_msg_id = utils_ref.document('last_booked_msg').get().to_dict()['id']

    if reaction.message.id != last_booked_msg_id:
        return

    if reaction.emoji == '👍':
        user_doc = users_ref.document(str(user.id))
        if user_doc.get().exists:
            user_info = user_doc.get().to_dict()
            if user_info['streak'] != 0:
                user_doc.update({'streak': user_info['streak'] - 1,
                                 'last_streak': user_info['streak'] - 1})
        else:
            add_user_to_db(user)

    elif reaction.emoji == '👎':
        user_doc = users_ref.document(str(user.id))
        if user_doc.get().exists:
            user_info = user_doc.get().to_dict()
            user_doc.update({'streak': user_info['last_streak']})
        else:
            add_user_to_db(user)


def add_user_to_db(user):
    if user.discriminator != 0:
        discriminator = '#' + user.discriminator
    else:
        discriminator = ''

    user_doc = users_ref.document(str(user.id))
    user_doc.set({
        'username': user.name + discriminator,
        'nickname': user.display_name,
        'streak': 0
    })


bot.run(token)

