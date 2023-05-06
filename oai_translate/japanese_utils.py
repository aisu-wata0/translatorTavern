
import re
import pykakasi

kakasi = pykakasi.kakasi()

keep_orig = {"「", "！", "」"}
re_kanji = r"一-龠"
re_hiragana = r"ぁ-ゔ"
re_katakana = r"ァ-ヴー"
re_ideo = r"々〆〤ヶ"
japanese_text_regex = fr'[{re_kanji}{re_hiragana}{re_katakana}{re_ideo}]+'
japanese_kanji_text_regex = fr'[{re_kanji}]+'

def get_romaji(item):
	if any((k in item['orig'] for k in keep_orig)):
		return item['orig']
	return item['hepburn']

def kanji_to_romaji(text):
	result = kakasi.convert(text)
	return ' '.join(get_romaji(i) for i in result)

def replace_japanese_with_romaji(match):
	return " " + kanji_to_romaji(match.group())

def convert_japanese_to_romaji(text, kanji_only=False):
	_japanese_text_regex = japanese_text_regex
	if kanji_only:
		_japanese_text_regex = japanese_kanji_text_regex
	return re.sub(_japanese_text_regex, replace_japanese_with_romaji, text)
