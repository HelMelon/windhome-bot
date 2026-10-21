import os
import asyncio
import aiohttp
import ujson
import datetime
from aiogram.client.default import DefaultBotProperties
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command, CommandStart
from aiogram.types import Message
from aiogram.utils import callback_answer
from aiogram.utils.formatting import (Bold, as_list, as_marked_section)
from datetime import date
from aiogram.utils.media_group import MediaGroupBuilder
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram import F
import requests    

TOKEN = os.environ.get("BOT_TOKEN", "")
URL = 'https://eri2.nca.by/guest/abandonedObject/'
dp = Dispatcher()

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    kb = [
        [
            types.KeyboardButton(text="Квартиры"),
            types.KeyboardButton(text="Заброшки")
        ],
    ]
    keyboard = types.ReplyKeyboardMarkup(
        keyboard=kb,
        resize_keyboard=True,
        input_field_placeholder="Выберите тип объекта"
    )
    await message.answer("Какой тип жилья ищем?", reply_markup=keyboard)

@dp.message(F.text.lower() == "квартиры")
async def check_appartments(message: Message) -> None:

    async with aiohttp.ClientSession(json_serialize=ujson.dumps) as session:
        async with session.get('https://api.kufar.by/search-api/v2/search/rendered-paginated?blc=v.or%3A1&cat=1010&cur=BYR&flrp=v.or%3A1&gtsy=country-belarus~province-gomelskaja_oblast~locality-gomel&lang=ru&prc=r%3A0%2C15000000&rms=v.or%3A3&size=30&sls=r%3A40%2C1000&typ=sell',
                            ) as resp:
                allapts = await resp.json()                
                for apt in allapts['ads']:
                    # apts_album_builder = MediaGroupBuilder()
                    # imageslength = len(apt['images'])
                    # imageslist = apt['images']
                    # del imageslist[10:imageslength]

                    # for img in imageslist:
                    #     if img['path']:
                    #         fullurlimg = f'https://rms1.kufar.by/v1/gallery/{img['path']}'
                    #         apts_album_builder.add_photo(media=fullurlimg)

                    for param in apt['ad_parameters']:
                        if param['pl'] == 'Координаты':
                            coord = param['v']
                        if param['pl'] == 'Общая площадь':
                            totalarea = str(param['v'])
                        if param['pl'] == 'Жилая площадь':
                            livearea = str(param['v'])
                        if param['pl'] == 'Балкон':
                            balcony = param['vl']
                        if param['pl'] == 'Этаж':
                            floor = param['vl'][0]
                        if param['pl'] == 'Этажность дома':
                            numberofstoreys = param['vl']
                    for acc_param in apt['account_parameters']:
                        if acc_param['pl'] == 'Адрес':
                            address = acc_param['v']
                        if acc_param['pl'] == 'Имя':
                            realt = 'агентство'
                        else: 
                            realt = 'владелец'    

                    content = as_list(
                        as_marked_section(
                            f'{apt['subject']}',
                            f'Адрес: {address}',
                            f'Общая площадь: {totalarea} кв.м.',
                            f'Жилая площадь: {livearea} кв.м.',
                            f'Балкон: {balcony}',
                            f'Сылка: {apt['ad_link']}',
                            f'Этаж: {floor} из {numberofstoreys}',
                            f'Продаёт {realt}',
                            f'Цена вопроса: {str(apt['price_byn'])[0:3]} {str(apt['price_byn'][3:6])} BYN',
                            marker="🏠 ",
                        )
                    )
                    await message.answer(**content.as_kwargs())
                    await message.answer_location(coord[1], coord[0])
                    # await message.answer_media_group(media=apts_album_builder.build())                

@dp.message(F.text.lower() == "заброшки")
async def check_abandoned_houses(message: Message) -> None:

    async with aiohttp.ClientSession(json_serialize=ujson.dumps) as session:
        async with session.post('https://eri2.nca.by/api/guest/abandonedObject/search', json=
                                {
                                    'ateId': 9967, 
                                    'parentId': 9397,
                                    'abandonedObjectTypeId': 1, 
                                    'stateSearchCategoryId': 2, 
                                    'withoutStreets': False, 
                                    'destroyed': False, 
                                    'emergency': False, 
                                    'oneBasePrice': True,
                                    'stateTypeId': 16
                                }) as resp:
            allhouses = await resp.json()
            for val in allhouses['data']['content']:
                    # album_builder = MediaGroupBuilder(
                    #   caption="Несколько фото объекта"
                    # )
                    urltohouse = f'https://eri2.nca.by/api/guest/abandonedObject/{str(val['id'])}'
                    async with session.post(f'https://eri2.nca.by/api/guest/abandonedObject/{str(val['id'])}/forView') as separatehouse:
                        houseinfo = await separatehouse.json()
                        if len(houseinfo['data']['images']) != 0:
                            for img in houseinfo['data']['images']:
                                fulllink = f'https://eri2.nca.by/api/images/abandonedObject/{img['path']}'
                                album_builder.add_photo(
                                    media=fulllink
                                )
                    content = as_list(
                                as_marked_section(
                                    Bold(val['abandonedObjectType']),
                                    f'Статус: {val['abandonedObjectStateType']}',
                                    f'Дата осмотра: {str(date.fromtimestamp(val['inspectionDate']/1000.0))}',
                                    f'Местонахождение: {val['position']}',
                                    f'Площадь: {str(round(houseinfo['data']['square']))} кв.м.',
                                    f'Стоимость: {str(round(houseinfo['data']['stateList'][0]['moneyAmount']))} рублей',
                                    urltohouse,
                                    marker="✅ ",
                                )
                    )
                    await message.answer(**content.as_kwargs())
                    await message.answer_media_group(media=album_builder.build())

async def main() -> None:
    bot = Bot(token=TOKEN)
    await dp.start_polling(bot)

if __name__ == '__main__':
    asyncio.run(main())

   
