#! python

import threading
import logging
import json
from datetime import datetime
from typing import Union
from collections.abc import Iterable

import clipb

from oai_translate import openai_utils
from oai_translate import oai_settings
from oai_translate import japanese_utils

formatting_necessary = False
formatting_care = False
jailbreak = False
max_tokens_relative_to_input = 1.5
print_romaji = True
use_tr_cache = True

start_from_history_file = ""
# start_from_history_file = "history/history copy 2.json"

history_og = []
history_tr = []
tr_cache = {}
history_cut = 0
LOCK = threading.Lock()

def binary_search(minimum, maximum, func):
	mi, ma = minimum, maximum
	while minimum <= maximum:
		mid = (minimum + maximum) // 2
		if func(mid):
			maximum = mid - 1
		else:
			minimum = mid + 1
	return max(mi, min(ma, minimum))


def find_history_cut(new_text):
	global history_cut
	def check_size_f(history_cut_):
		over_size = openai_utils.prompt_check_size_history_text(
			new_text, history_og[history_cut_:], history_tr[history_cut_:], max_tokens_relative_to_input=max_tokens_relative_to_input, jailbreak=jailbreak)
		too_big = over_size > 0
		return not too_big

	too_big = not check_size_f(history_cut)
	if too_big:
		history_cut = binary_search(history_cut, len(history_og), check_size_f)
	return history_cut

if start_from_history_file:
	with open(start_from_history_file, mode="r", encoding="utf-8") as f:
		history_json = json.load(f)
		for t in history_json:
			history_og.append(t[0])
			history_tr.append(t[1])
			if t[0] in tr_cache:
				tr_cache[t[0]].append(t[1])
			else:
				tr_cache[t[0]] = [t[1]]
	
	print("find_history_cut...")
	history_cut = find_history_cut("whatever")
	print(f"finished, history_cut = {history_cut}\n")

def history_og_current():
	return history_og[history_cut:]

def history_tr_current():
	return history_tr[history_cut:]

def get_timestamp():
	return datetime.now().isoformat(timespec='seconds').replace(':', '_')

def get_timestamp_name(name, ext="json", directory="history"):
	now = get_timestamp()
	name = f"{directory}/{name}_{now}.{ext}"
	return name

def write_to_file(obj, name, timestamp: Union[str, bool]=False, directory="history", json_write=False, timestamp_directory="history/old"):
	ext = "log"
	if json_write:
		ext = "json"

	if timestamp:
		d = timestamp_directory if timestamp_directory is not None else directory
		if isinstance(timestamp, str):
			name = f"{d}/{name}-{timestamp}.{ext}"
		else:
			name = get_timestamp_name(name, f".{ext}", directory=d)
	else:
		name = f"{directory}/{name}.{ext}"

	with open(name, mode="w", encoding="utf-8") as f:
		if json_write:
			json.dump(obj, f, ensure_ascii=False, indent=2)
		else:
			if isinstance(obj, Iterable):
				for o in obj:
					try:
						f.write(str(o) + "\n\n")
					except Exception as e:
						logging.exception("write error")
			else:
				try:
					f.write(str(obj))
				except:
					pass

def save_state(timestamp=False, directory="history"):
	if timestamp:
		timestamp = get_timestamp()

	flattened_interleaved_history = [elem for (o, t) in zip(history_og, history_tr) for elem in (o, t)]

	write_to_file(history_og, "history_og", timestamp=timestamp, directory=directory, json_write=True)
	write_to_file(history_tr, "history_tr", timestamp=timestamp, directory=directory, json_write=True)
	write_to_file([(o, t) for (o, t) in zip(history_og, history_tr)], "history", timestamp=timestamp, directory=directory, json_write=True)
	write_to_file(history_og, "history_og", timestamp=timestamp, directory=directory, json_write=False)
	write_to_file(history_tr, "history_tr", timestamp=timestamp, directory=directory, json_write=False)
	write_to_file(flattened_interleaved_history, "history", timestamp=timestamp, directory=directory, json_write=False)

	replacer = ['\n', r'\n']
	write_to_file((f"{o.replace(*replacer)}={t.replace(*replacer)}" for (o, t) in zip(history_og, history_tr)), "history_unity", timestamp=timestamp, directory=directory, json_write=False)




def translate(new_text, temperature=None):
	global history_cut
	global tr_cache
	if not new_text:
		return ""
	with LOCK:
		if len(history_og) > 1 and new_text.replace("\n", "") == history_og[-1].replace("\n", ""):
			return history_tr[-1]
		new_text = new_text.replace('\r\n', '\n')

		print_text = new_text
		if print_romaji:
			def collate(a, b):
				a_list = a.split('\n')
				b_list = b.split('\n')
				c_list = []
				for i in range(len(a_list)):
					c_list.append(a_list[i])
					c_list.append(b_list[i])
				return '\n'.join(c_list)
			print_text = collate(new_text, japanese_utils.convert_japanese_to_romaji(new_text))
		print(f'\n=-=\n{print_text}\n=-= {len(history_og)} - {history_cut}', flush=True)

		try:
			content = ""
			if use_tr_cache and new_text in tr_cache:
				content = tr_cache[new_text]
			else:
				history_cut = find_history_cut(new_text)
				content = openai_utils.send_openai_message_history_text(
					new_text, history_og_current(), history_tr_current(), max_tokens_relative_to_input=max_tokens_relative_to_input, jailbreak=jailbreak, formatting_care=formatting_care, formatting_necessary=formatting_necessary, temperature=temperature if temperature is not None else oai_settings.oai_settings["temp_openai"], retries=1)
			print(content)
			history_og.append(new_text)
			history_tr.append(content)
			tr_cache[new_text] = content
			save_state()
			return content
		except RuntimeError as e:
			print(f"Error translating ```\n{new_text}\n```\n{e}")
		return None

def translate_new_text(new_text):
	thread = threading.Thread(target=translate, args=(new_text,))
	return thread

from flask import Flask, request

log = logging.getLogger('werkzeug')
log.setLevel(logging.WARN)
app = Flask(__name__)
translation = ""

@app.route('/translate', methods=['GET'])
def custom_translate():
	text = request.args.get('text')
	if isinstance(text, str):
		translation = translate(text)  # replace this with your actual translation function
		return translation


def run_flask():
	app.run()

from werkzeug.serving import make_server

class ServerThread(threading.Thread):
	def __init__(self, app):
		threading.Thread.__init__(self)
		self.server = make_server('127.0.0.1', 5000, app)
		self.ctx = app.app_context()
		self.ctx.push()

	def run(self):
		self.server.serve_forever()

	def shutdown(self):
		self.server.shutdown()

if __name__ == "__main__":
	import argparse
	# Instantiate the parser
	parser = argparse.ArgumentParser(
		description='script description\n'
		'python ' + __file__ + ' arg1 example usage',
		formatter_class=argparse.RawTextHelpFormatter)

	parser.add_argument('-v', '--verbose', action='store_true',
						help='verbose')
	# parser.add_argument('link', nargs='?',
	# 					help='video link')
	# parser.add_argument('--download_dir', nargs='?', default=os.path.dirname(__file__) + '/downloads/',
	# 					help='video link')
	# parser.add_argument('-yt', '--only_youtube', action='store_true',
	# 					help='youtube only')

	# Parse arguments
	args = parser.parse_args()

	print(f"Watching clipboard.", flush=True)
	print(f"Keys:", flush=True)
	print(f"\t'q': Quit", flush=True)
	print(f"\t'p': Pause", flush=True)
	print(f"\t'u': Unpause", flush=True)
	print(f"\t'c': Clear translation history", flush=True)
	print(f"\t'int': Delete last `int` from history", flush=True)

	# wait for links in clipboard
	def predicate(new_text):
		# this can filter new text if you want
		# just return False when you don't want to translate it
		return True

	watcher = clipb.ClipboardWatcher(
		predicate, translate_new_text, 0.01)

	watcher.start()
	
	# flask_thread = ServerThread(app)
	flask_thread = None
	# flask_thread.start()
	temperature = 0.4

	while True:
		try:
			inp = input("...")
			inp = inp.lower()
			# # Keybindings
			# Pause
			if inp == "p":
				print('pausing...', flush=True)
				watcher.pause()
			# Unpause
			if inp == "u":
				print('continuing...', flush=True)
				watcher.unpause()
			# Clear history
			if inp == "c":
				print('clearing history...', flush=True)
				save_state(True)
				history_og = []
				history_tr = []
			# Toggle jailbreak
			if inp == "j":
				jailbreak = not jailbreak
				print(f"jailbreak {jailbreak}")
			if inp == "reload_oai_settings":
				print(f"reload_oai_settings")
				openai_utils.reload_oai_settings()
			# change proxy
			if inp == "reverse_proxy":
				reverse_proxy = input("reverse_proxy:")
				print(f"reverse_proxy: {reverse_proxy}")
				openai_utils.change_reverse_proxy(reverse_proxy)
			# change api key
			if inp == "api_key":
				api_key = input("api_key:")
				print(f"api_key: {api_key}")
				openai_utils.change_api_key(api_key)
			# Retry translation
			if inp == "r":
				if len(history_og) < 1:
					continue
				temp_inp = input(f"temperature (Empty to {temperature}): ")
				temp = temperature
				def is_float(string):
					try:
						float(string)
						return True
					except ValueError:
						return False
				if is_float(temp_inp):
					temp = float(temp_inp)
				new_text = history_og[-1]
				if new_text in tr_cache:
					del tr_cache[new_text]
				history_og = history_og[:-1]
				history_tr = history_tr[:-1]
				translate(new_text, temperature=temperature)
			# Show history
			if inp == "s":
				print(f'Showing history... length = {len(history_og)} how far back (int)?', flush=True)
				show_qnt = input("...")
				if not show_qnt.isdigit():
					print(f'Invalid number {show_qnt}', flush=True)
					continue
				try:
					for (o, t) in zip(history_og[-int(show_qnt):], history_tr[-int(show_qnt):]):
						print("")
						print(o)
						print("")
						print(t)
				except Exception as e:
					logging.exception("Exception while printing history")
			if inp.isdigit():
				if int(inp) > 0:
					print('clearing history...', flush=True)
					save_state(True)
					history_og = history_og[:-int(inp)]
					history_tr = history_tr[:-int(inp)]
					save_state()
			# Quit / Break out
			if inp == "q":
				break
			# #
		except KeyboardInterrupt:
			print("Clipboard watcher interrupted.")
			break
		except Exception as e:
			logging.exception(f"Caught exception while waitning for user input")
			pass

	print('stopping...', flush=True)
	save_state(True)
	watcher.stop()
	watcher.join()

	if flask_thread:
		flask_thread.shutdown()
