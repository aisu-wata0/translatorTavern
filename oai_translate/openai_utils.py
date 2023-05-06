
import json
import requests
import logging
import re
import random
from http.cookies import SimpleCookie
from pathlib import Path
from datetime import datetime
import importlib

from . import oai_settings
from . import japanese_utils

url = f"http://localhost:{8000}"
token = None

jailbreak_filename = "jailbreak-openai.txt"
prompt_openai_filename = "prompt-openai.txt"

jailbreak_file_path = next(Path(".").rglob(jailbreak_filename))
prompt_openai_file_path = next(Path(".").rglob(prompt_openai_filename))

with open(jailbreak_file_path, 'r', encoding='utf-8') as j:
	jailbreak_text = j.read()

with open(prompt_openai_file_path, 'r', encoding='utf-8') as j:
	prompt_text = j.read()

system_new_start = {
	"role": "system",
	"content": "[Start a new translation]"
}

def reload_oai_settings():
	importlib.reload(oai_settings)

def get_jailbreak_msg():
	print("JAILBREAK")
	return {
		"role": "system",
		"content": jailbreak_text
	}


def get_user_msg(content):
	if isinstance(content, list):
		return [{
			"role": "user",
			"content": c,
		} for c in content]
	return {
		"role": "user",
		"content": content,
	}

def get_translation_msg(content):
	if isinstance(content, list):
		return [{
			"role": "assistant",
			"content": c,
		} for c in content]
	return {
		"role": "assistant",
		"content": content,
	}

def parse_prompt(text):
	initial_prompt = []

	# Isolate initial strings before any entry.
	try:
		initial_text, chatters_text = text.strip().split('\n\n<START>', 1)
	except ValueError:
		initial_text = text.strip()
		chatters_text = None
		
	initial_prompt.append({
		"role": "system",
		"content": initial_text
	})
	if not chatters_text:
		return (initial_prompt, [])

	entries = []
	# Looping through remaining lines separated by splitting,
	# we search for any valid key or chatter substring.
	
	for chat in chatters_text.split('\n\n<START>'):
		entries.append(system_new_start)
		
		last_entry_content = None
		for line in chat.split('\n'):
			match = re.fullmatch(r'\[\[(\w+)\]\]: (.+)', line)
			if match is not None:
				k,v = match.groups()
				last_entry_content = {
					"role": k,
					"content": v, 
					# 'name': f'example_{k}'
				}
				entries.append(last_entry_content)
			elif last_entry_content is not None:
				last_entry_content['content'] += f"\n{line}"
			else:
				# This is an edge case where there are non-matched lines after <START>
				pass
		
	return (entries, entries)

prompt_base, examples = parse_prompt(prompt_text)

s = requests.session()

def update_csrf_token():
	global token
	response = s.get(f'{url}/csrf-token')
	data = response.json()
	token = data['token']
	set_cookie = SimpleCookie(response.headers["set-cookie"])
	for key, morsel in set_cookie.items():
		s.cookies[key] = morsel.value

def get_headers():
	if not token:
		update_csrf_token()
	return {
		'Content-Type': 'application/json',
		"X-CSRF-Token": token,
	}




def get_prompt_history(history_user, history_assistant):
	prompt_history = []
	if len(history_user) != len(history_assistant):
		raise RuntimeError("This function assumes len(history_user) != len(history_assistant), as it interweaves them")
	for i in range(len(history_user)):
		prompt_history.append(get_user_msg(history_user[i]))
		prompt_history.append(get_translation_msg(history_assistant[i]))
	return prompt_history


def get_prompt(user_msg, prompt_history, jailbreak=True):
	prompt = [*prompt_base, *examples,]
	prompt += [system_new_start, *prompt_history, user_msg]
	if jailbreak:
		prompt += [get_jailbreak_msg()]
	return prompt

def send_openai_message_history_text(text, history_og, history_tr, max_tokens=oai_settings.oai_settings["openai_max_tokens"], max_tokens_relative_to_input=None, temperature=float(oai_settings.oai_settings["temp_openai"]), jailbreak=True, formatting_care=True, formatting_necessary=False, retries=4, temp_increase=lambda x, t: (float(x) + float(t) * 0.2)):
	prompt_history = get_prompt_history(history_og, history_tr)

	if max_tokens_relative_to_input:
		input_tokens = count_tokens(get_user_msg(text))
		max_tokens = int(max_tokens_relative_to_input * float(input_tokens))

	prompt = get_prompt(get_user_msg(text), prompt_history, jailbreak=jailbreak)

	maintain_formatting_from = text
	if not formatting_care:
		maintain_formatting_from = None
	return send_openai_request(prompt, max_tokens=max_tokens, temperature=temperature, maintain_formatting_from=maintain_formatting_from, formatting_necessary=formatting_necessary, retries=retries, temp_increase=temp_increase)


def send_openai_text(text, max_tokens=oai_settings.oai_settings["openai_max_tokens"], max_tokens_relative_to_input=None, temperature=float(oai_settings.oai_settings["temp_openai"]), jailbreak=True, formatting_care=True, formatting_necessary=False, retries=4, temp_increase=lambda x, t: (float(x) + float(t) * 0.2)):
	prompt = [*prompt_base, *examples]
	prompt += [system_new_start, get_user_msg(text)]
	if jailbreak:
		prompt += [get_jailbreak_msg()]

	if max_tokens_relative_to_input:
		input_tokens = count_tokens(get_user_msg(text))
		max_tokens = int(max_tokens_relative_to_input * float(input_tokens))

	maintain_formatting_from = text
	if not formatting_care:
		maintain_formatting_from = None
	return send_openai_request(prompt, max_tokens=max_tokens, temperature=temperature, maintain_formatting_from=maintain_formatting_from, formatting_necessary=formatting_necessary, retries=retries, temp_increase=temp_increase)


def check_for_blacklisted_text(text, blacklist=oai_settings.oai_settings["text_blacklist"]):
	text_lower = text.lower()
	for item in blacklist:
		if isinstance(item, tuple):
			 # Check if all tuple elements are in text
			if all(x.lower() in text_lower for x in item):
				return True
		elif isinstance(item, dict):
			if "regex" in item:
				if re.search(item["regex"], text):
					return True
		elif item.lower() in text_lower:
			return True
	return False

def change_reverse_proxy(proxy):
	 oai_settings.oai_settings["reverse_proxy"] = proxy

def change_api_key(api_key):
	 oai_settings.oai_settings["api_key_openai"] = api_key

proxy_idx = 0

def send_openai_request(openai_msgs_tosend, max_tokens=oai_settings.oai_settings["openai_max_tokens"], temperature=float(oai_settings.oai_settings["temp_openai"]), maintain_formatting_from=None, formatting_necessary=False, retries=4, temp_increase=lambda x, t: (float(x) + float(t) * 0.2)):
	global proxy_idx

	temperature_initial = temperature
	first_try = None
	attempt = 0
	attempts = []
	attempts_data = []

	reverse_proxy = oai_settings.oai_settings['reverse_proxy']
	max_context = oai_settings.oai_settings["openai_max_context"]
	model = oai_settings.oai_settings["openai_model"]
	filter_kanji = oai_settings.oai_settings["filter_kanji"]

	reverse_proxy_list = oai_settings.oai_settings['reverse_proxy']
	proxy_idx = 0
	if isinstance(oai_settings.oai_settings['reverse_proxy'], list):
		reverse_proxy_list = oai_settings.oai_settings['reverse_proxy'].copy()
		if oai_settings.oai_settings['reverse_proxy_suffle']:
			random.shuffle(reverse_proxy_list)
		proxy_idx = min(proxy_idx, len(reverse_proxy_list) - 1)

	while attempt < retries + 1:  # Add 1 to retries to include the initial attempt
		temperature = temp_increase(temperature_initial, attempt)
		attempt += 1
		if isinstance(oai_settings.oai_settings['reverse_proxy'], list):
			reverse_proxy = reverse_proxy_list[proxy_idx]["url"]
			max_context = reverse_proxy_list[proxy_idx]["settings"]["openai_max_context"]
			model = reverse_proxy_list[proxy_idx]["settings"]["openai_model"]
			filter_kanji = reverse_proxy_list[proxy_idx]["settings"]["filter_kanji"]

		openai_msgs_tosend_filtered = openai_msgs_tosend
		if filter_kanji:
			openai_msgs_tosend_filtered = [{
				**m,
				"content": japanese_utils.convert_japanese_to_romaji(m["content"], kanji_only=True)}
				for m in openai_msgs_tosend]
		
		context_tokens = count_tokens(openai_msgs_tosend_filtered)

		if (context_tokens + max_tokens) > max_context:
			max_tokens = max_context - context_tokens
			logging.warn(f"MAX CONTEXT EXCEEDED, LOWER MAX TOKENS OR SHORTEN MESSAGE context_tokens={context_tokens} max_tokens={max_tokens}")
		
		generate_data = {
			"messages": openai_msgs_tosend_filtered,
			"model": model,
			"temperature": temperature,
			"frequency_penalty": float(oai_settings.oai_settings["freq_pen_openai"]),
			"presence_penalty": float(oai_settings.oai_settings["pres_pen_openai"]),
			"max_tokens": max_tokens,
			"stream": oai_settings.oai_settings.get("stream_open_ai", False),
			"reverse_proxy": reverse_proxy,
		}
		try:
			headers = get_headers()
			response = s.post(f'{url}/generate_openai', data=json.dumps(generate_data), headers=headers)
			response.raise_for_status()  # Raises exception on non-successful status codes
			data = response.json()
			attempts_data.append(data)
			if 'error' in data:
				raise RuntimeError(data)
			if 'choices' not in data:
				raise RuntimeError(data)
			content = data["choices"][0]["message"]["content"]
			attempts.append(content)
			if check_for_blacklisted_text(content):
				retries += 1
				logging.info(f'Content blacklisted... (Attempt {attempt+1}/{retries+1})')
				continue  # Always retry if blacklisted text is found
			if maintain_formatting_from is not None:
				try:
					if first_try is None:
						first_try = content
					content = tr_formatting_check(maintain_formatting_from, content)
				except Exception as err:
					print("Formatting fail: " + str(err))
					continue
			
			return content  # Return content if no blacklisted text is found
		except requests.exceptions.HTTPError as err:
			if err.response.status_code == 403:
				update_csrf_token()
			logging.error(f'HTTP Error: {err}')
			if attempt < retries:
				logging.info(f'Retrying request... (Attempt {attempt+1}/{retries+1})')
		except Exception as err:
			# logging.error(f'Unexpected error: {err}')
			if attempt < retries:
				logging.info(f'Retrying request... (Attempt {attempt+1}/{retries+1})')

		if isinstance(reverse_proxy_list, list):
			proxy_idx += 1
			if proxy_idx  < len(reverse_proxy_list):
				attempt -= 1
			else:
				proxy_idx = 0
			reverse_proxy = reverse_proxy_list[proxy_idx]

	if first_try is not None and maintain_formatting_from is not None and not formatting_necessary:
		return first_try
	
	if attempts:
		now = datetime.now().isoformat(timespec='seconds').replace(':', '_')
		with open(f"send_openai_request-attempts-{now}.json", mode="w", encoding="utf-8") as f:
			json.dump(attempts, f, ensure_ascii=False, indent=2)
		with open(f"send_openai_request-attempts_data-{now}.json", mode="w", encoding="utf-8") as f:
			json.dump(attempts, f, ensure_ascii=False, indent=2)
	raise RuntimeError("retries exhausted")

token_cache = {}


def prompt_check_size_history_text(text, history_user, history_assistant, max_tokens=oai_settings.oai_settings["openai_max_tokens"], 
max_tokens_relative_to_input=None, jailbreak=True):
	return prompt_check_size_history(get_user_msg(text), get_prompt_history(history_user, history_assistant), max_tokens=max_tokens, max_tokens_relative_to_input=max_tokens_relative_to_input, jailbreak=jailbreak)


def prompt_check_size_history(openai_msg, prompt_history, max_tokens=oai_settings.oai_settings["openai_max_tokens"], max_tokens_relative_to_input=None, jailbreak=True):
	prompt = get_prompt(openai_msg, prompt_history, jailbreak=jailbreak)

	if max_tokens_relative_to_input:
		input_tokens = count_tokens(openai_msg)
		max_tokens = int(max_tokens_relative_to_input * float(input_tokens))

	return prompt_check_size(prompt, max_tokens=max_tokens)

def prompt_check_size(prompt, max_tokens=oai_settings.oai_settings["openai_max_tokens"]):
	context_tokens = count_tokens(prompt)
	max_context = oai_settings.oai_settings["openai_max_context"]
	if (context_tokens + max_tokens) > max_context:
		max_tokens_old = max_tokens
		max_tokens = max_context - context_tokens
		return max_tokens_old - max_tokens
	return 0
		

def count_tokens(messages, full=False, chat_id=-1):
	if chat_id not in token_cache:
		token_cache[chat_id] = {}
	messages = [messages] if not isinstance(messages, list) else messages
	token_count = 0
	for message in messages:
		if oai_settings.oai_settings["filter_kanji"]:
			message["content"] = japanese_utils.convert_japanese_to_romaji(message["content"], kanji_only=True)

		hash_message_string = str(hash(message["content"]))

		cached_count = token_cache[chat_id].get(hash_message_string)
		if cached_count is not None:
			token_count += cached_count
		else:
			data_to_tokenise = [message]
			headers = get_headers()
			response_data = s.post(
				f'{url}/tokenize_openai?model={oai_settings.oai_settings["openai_model"]}', data=json.dumps(data_to_tokenise), headers=headers)
			response_data = response_data.json()
			current_token_count = response_data["token_count"]
			token_cache[chat_id][hash_message_string]  = current_token_count 
			token_count += current_token_count
	if not full: 
		token_count -= 2
	return token_count


def phrase_formatting_check(og, tr):
	for s in ["「", '"', "'",]:
		if og[0] == s and tr[0] != s:
			raise RuntimeError(f"if og[0] == s and tr[0] != s: {s} og::{og} tr::{tr}")
	for e in ["」", '"', "'",]:
		if og[-1] == e and tr[-1] != e:
			raise RuntimeError(f"if og[-1] == e and tr[-1] != e: {e} og::{og} tr::{tr}")
	return True

def tr_formatting_check(og, tr):
	# Checks if the formatting of the original is the same as the translation
	# Same number of lines, and same quoting on the same lines
	# Also fixes the trailing whitespace in the translation to be equal to the original
	og_trailing_whitespace = None
	if isinstance(og, str):
		og_rstrip = og.rstrip()
		og_trailing_whitespace_length = len(og_rstrip) - len(og)
		og_trailing_whitespace = ""
		if og_trailing_whitespace_length > 0:
			og_trailing_whitespace = og[len(og) - len(og_rstrip):]
		og = og_rstrip.split("\n")
	if isinstance(tr, str):
		tr = tr.rstrip().split("\n")
	if len(og) != len(tr):
		raise RuntimeError(f"if len(og) != len(tr): og::{og} tr::{tr}")
	for i in range(len(og)):
		if og[i].count(r'\n') != tr[i].count(r'\n'):
			raise RuntimeError(f"og[i].count(r'\n') != tr[i].count(r'\n'): og::{og} tr::{tr}")
		if len(og[i]) > 0:
			if len(tr[i]) == 0:
				raise RuntimeError(f"len(og[i]) > 0: len(tr[i]) == 0: og::{og} tr::{tr}")
			phrase_formatting_check(og[i], tr[i])
		else:
			if len(tr[i]) > 0:
				raise RuntimeError(f"len(og[i]) <= 0: if len(tr[i]) > 0: og::{og} tr::{tr}")
	tr_checked =  "\n".join(tr)
	if og_trailing_whitespace:
		tr_checked += og_trailing_whitespace
	return tr_checked
