
import errno
import json
import logging
import os
import re
import uuid

from common.googleapis import GoogleAPIClient


class UploadBackend(object):
	"""Represents a place a video can be uploaded,
	and maintains any state needed to perform uploads.

	Config args for the backend are passed into __init__ as kwargs,
	along with credentials as the first arg.

	Should have a method upload_video(title, description, tags, data).
	Title, description and tags may have backend-specific meaning.
	Tags is a list of string.
	Data is an iterator of strings.
	It should return (video_id, video_link).

	If the video must undergo additional processing before it's available
	(ie. it should go into the TRANSCODING state), then the backend should
	define the 'needs_transcode' attribute as True.
	If it does, it should also have a method check_status(ids) which takes a
	list of video ids and returns a list of the ones who have finished processing.

	The upload backend also determines the encoding settings for the cutting
	process, this is given as a list of ffmpeg args
	under the 'encoding_settings' attribute.
	If this is None, instead uses the 'fast cut' strategy where nothing
	is transcoded.
	"""

	needs_transcode = False

	# reasonable default if settings don't otherwise matter
	encoding_settings = ['-f', 'mp4']

	def upload_video(self, title, description, tags, data):
		raise NotImplementedError

	def check_status(self, ids):
		raise NotImplementedError


class Youtube(UploadBackend):
	"""Represents a youtube channel to upload to, and settings for doing so.
	Config args besides credentials:
		hidden:
			If false, video is public. If true, video is unlisted. Default false.
		category_id:
			The numeric category id to set as the youtube category of all videos.
			Default is 23, which is the id for "Comedy". Set to null to not set.
		language:
			The language code to describe all videos as.
			Default is "en", ie. English. Set to null to not set.
	"""

	needs_transcode = True
	encoding_settings = [
		# Youtube's recommended settings:
		'-codec:v', 'libx264', # Make the video codec x264
		'-crf', '21', # Set the video quality, this produces the bitrate range that YT likes
		'-bf', '2', # Have 2 consecutive bframes, as requested
		'-flags', '+cgop', # Use closed GOP, as requested
		'-pix_fmt', 'yuv420p', # chroma subsampling 4:2:0, as requrested
		'-codec:a', 'aac', '-strict', '-2', # audio codec aac, as requested
		'-b:a', '384k' # audio bitrate at 348k for 2 channel, use 512k if 5.1 audio
		'-r:a', '48000', # set audio sample rate at 48000Hz, as requested
		'-movflags', 'faststart', # put MOOV atom at the front of the file, as requested
	]

	def __init__(self, credentials, hidden=False, category_id=23, language="en"):
		self.logger = logging.getLogger(type(self).__name__)
		self.client = GoogleAPIClient(
			credentials['client_id'],
			credentials['client_secret'],
			credentials['refresh_token'],
		)
		self.hidden = hidden
		self.category_id = category_id
		self.language = language

	def upload_video(self, title, description, tags, data):
		json = {
			'snippet': {
				'title': title,
				'description': description,
				'tags': tags,
			},
		}
		if self.category_id is not None:
			json['snippet']['categoryId'] = self.category_id
		if self.language is not None:
			json['snippet']['defaultLanguage'] = self.language
			json['snippet']['defaultAudioLanguage'] = self.language
		if self.hidden:
			json['status'] = {
				'privacyStatus': 'unlisted',
			}
		resp = self.client.request('POST',
			'https://www.googleapis.com/upload/youtube/v3/videos',
			params={
				'part': 'snippet,status' if self.hidden else 'snippet',
				'uploadType': 'resumable',
			},
			json=json,
		)
		resp.raise_for_status()
		upload_url = resp.headers['Location']
		resp = self.client.request('POST', upload_url, data=data)
		resp.raise_for_status()
		id = resp.json()['id']
		return id, 'https://youtu.be/{}'.format(id)

	def check_status(self, ids):
		output = []
		# Break up into groups of 10 videos. I'm not sure what the limit is so this is reasonable.
		for i in range(0, len(ids), 10):
			group = ids[i:i+10]
			resp = self.client.request('GET',
				'https://www.googleapis.com/youtube/v3/videos',
				params={
					'part': 'id,status',
					'id': ','.join(group),
				},
			)
			resp.raise_for_status()
			for item in resp.json()['items']:
				if item['status']['uploadStatus'] == 'processed':
					output.append(item['id'])
		return output


class Local(UploadBackend):
	"""An "upload" backend that just saves the file to local disk.
	Needs no credentials. Config args:
		path:
			Where to save the file.
		url_prefix:
			The leading part of the URL to return.
			The filename will be appended to this to form the full URL.
			So for example, if you set "http://example.com/videos/",
			then a returned video URL might look like:
				"http://example.com/videos/my-example-video-1ffd816b-6496-45d4-b8f5-5eb06ee532f9.ts"
			If not given, returns a file:// url with the full path.
		write_info:
			If true, writes a json file alongside the video file containing
			the video title, description and tags.
			This is intended primarily for testing purposes.
	Saves files under their title, plus a random video id to avoid conflicts.
	Ignores description and tags.
	"""

	def __init__(self, credentials, path, url_prefix=None, write_info=False):
		self.path = path
		self.url_prefix = url_prefix
		self.write_info = write_info
		# make path if it doesn't already exist
		try:
			os.makedirs(self.path)
		except OSError as e:
			if e.errno != errno.EEXIST:
				raise
			# ignore already-exists errors

	def upload_video(self, title, description, tags, data):
		video_id = uuid.uuid4()
		# make title safe by removing offending characters, replacing with '-'
		title = re.sub('[^A-Za-z0-9_]', '-', title)
		filename = '{}-{}.ts'.format(title, video_id) # TODO with re-encoding, this ext must change
		filepath = os.path.join(self.path, filename)
		if self.write_info:
			with open(os.path.join(self.path, '{}-{}.json'.format(title, video_id))) as f:
				f.write(json.dumps({
					'title': title,
					'description': description,
					'tags': tags,
				}) + '\n')
		with open(filepath, 'w') as f:
			for chunk in data:
				f.write(chunk)
		if self.url_prefix is not None:
			url = self.url_prefix + filename
		else:
			url = 'file://{}'.format(filepath)
		return video_id, url