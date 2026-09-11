# ZinoQuotexSignalAI

بوت Telegram يستقبل Screenshot من الرسم البياني ويرسل تحليلًا بصيغة:
CALL / PUT / NO TRADE.

## المتغيرات السرية
لا تضع الرموز السرية داخل الكود. في Render أضف:
- TELEGRAM_TOKEN = Token البوت من BotFather
- GEMINI_API_KEY = مفتاح Gemini API

## التشغيل
Build:
pip install -r requirements.txt

Start:
python bot.py

## ملاحظة
البوت يقدم تحليلًا احتماليًا فقط ولا يضمن نتيجة الصفقة. لا ينفذ صفقات تلقائيًا.
