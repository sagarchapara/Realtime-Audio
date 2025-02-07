# example requires websocket-client library:
# pip install websocket-client

from asyncio import Queue
import asyncio
import base64
from dataclasses import dataclass, field
import os
import json
import threading
from typing import List
import websocket
import pyaudio
import traceback


OPENAI_API_KEY = os.environ.get("AZURE_OPEANI_API_KEY")

url = os.environ.get("AZURE_OPEANI_API_ENDPOINT")
headers = [
    "api-key:" + OPENAI_API_KEY,
    "OpenAI-Beta: realtime=v1"
]

@dataclass
class OpenAISessionProperties:
    modalities: List[str] = field(default_factory=lambda: ["text"])
    model: str = "gpt-4o-realtime-preview"
    instructions: str = "You are a helpful assistant."
    voice: str = "sage"
    input_audio_format: str = "pcm16"
    output_audio_format: str = "pcm16"
    input_audio_transcript: dict = field(default_factory=lambda: {"model": "whisper-1"})
    turn_detection: dict = field(default_factory=lambda: {
        "type": "server_vad",
        "threshold": 0.5,
        "prefix_padding_ms": 300,
        "silence_duration_ms": 500,
        "create_response": True
    })
    temperature: float = 0.8


class OpenAIWebSocketClient:
    FORMAT = pyaudio.paInt16
    CHANNELS = 1
    SEND_SAMPLE_RATE = 16000
    RECEIVE_SAMPLE_RATE = 24000
    CHUNK_SIZE = 1024
    def __init__(self, url, headers, session_properties: OpenAISessionProperties):
        self.url = url
        self.headers = headers
        self.output_audio = Queue(maxsize=20)
        self.in_audio = Queue()
        self.session_properties = session_properties

        self.should_run = True
        self.pya = pyaudio.PyAudio()


    def __del__(self):
        self.ws.close()

    def shouldTerminate(self, text: str):
        return "TERMINATE" in text

    def connect(self):
        self.ws = websocket.WebSocketApp(
            self.url,
            header=self.headers,
            on_open=self.on_open,
            on_message=self.on_message,
            on_error=self.on_error,
            on_close=self.on_close
        )

        self.ws.run_forever()

    def session_update(self, properties: OpenAISessionProperties):
        event = {
            "type": "session.update",
            "session": properties.__dict__
        }

        print(event)

        self.ws.send(json.dumps(event))

    def on_open(self, ws):
        print("Connected to OpenAI")
        # self.session_update(self.session_properties)

    def on_message(self, ws, message):
        server_event = json.loads(message)

        print(server_event["type"])

        # we need only partial audio and full text
        if server_event["type"] == "response.audio.delta" or server_event["type"] == "response.audio.done":
            audio = base64.b64decode(server_event["delta"])
            self.in_audio.put_nowait(audio)
        elif server_event["type"] == "response.content_part.added" or server_event["type"] == "response.content_part.done":
            part = server_event["part"]

            if part["type"] == "text":
                print(part["value"])
                if self.shouldTerminate(part["value"]):
                    self.should_run = False
                    self.ws.close()
            elif part["type"] == "audio":
                audio = base64.b64decode(part["value"])
                self.in_audio.put_nowait(audio)
            else:
                print("Skipping part: " + part["type"])
        elif server_event["type"] ==  "response.content_part.done":
            part = server_event["part"]

            if part["type"] == "text":
                print(part["value"])
                if self.shouldTerminate(part["value"]):
                    self.should_run = False
                    self.ws.close()            
        elif server_event["type"] == "input_audio_buffer.speech_started":
            # #remove all audio data from the queue
            # while not self.output_audio.empty():
            #     self.output_audio.get_nowait()
            pass
        elif server_event["type"] == "response.done":
            #get the final response
            pass
        else:
            print("Skipping event: " + server_event["type"])




    def on_error(self, ws, error):
        print(f"Error from OpenAI: {error}")
    
    def on_close(self, ws, close_status_code, close_msg):
        print(f"Closed connection to OpenAI with status code: {close_status_code}, message: {close_msg}")
        ws.close()

    async def stream_audio(self):
        while self.should_run:
            audio = await self.output_audio.get()

            audio_base64 = base64.b64encode(audio).decode("utf-8")
            event = {
                "type": "input_audio_buffer.append",
                "audio": audio_base64
            }

            self.ws.send(json.dumps(event))

    async def listen_audio(self):
        mic_info = self.pya.get_default_input_device_info()
        self.audio_stream = await asyncio.to_thread(
            self.pya.open,
            format=self.FORMAT,
            channels=self.CHANNELS,
            rate=self.SEND_SAMPLE_RATE,
            input=True,
            input_device_index=mic_info["index"],
            frames_per_buffer=self.CHUNK_SIZE,
        )
        if __debug__:
            kwargs = {"exception_on_overflow": False}
        else:
            kwargs = {}
        while self.should_run:
            data = await asyncio.to_thread(self.audio_stream.read, self.CHUNK_SIZE, **kwargs)
            # await self.output_audio.put({"data": data, "mime_type": "audio/pcm"})
            await self.output_audio.put(data)
        


    async def play_audio(self):
        stream = await asyncio.to_thread(
            self.pya.open,
            format=self.FORMAT,
            channels=self.CHANNELS,
            rate=self.RECEIVE_SAMPLE_RATE,
            output=True,
        )
        while self.should_run:
            bytestream = await self.in_audio.get()
            await asyncio.to_thread(stream.write, bytestream)

    
    async def start(self):

        thread = threading.Thread(target=self.connect)
        thread.start()

        await asyncio.sleep(1)

        try:
            async with asyncio.TaskGroup() as g:
                g.create_task(self.listen_audio())
                g.create_task(self.stream_audio())
                g.create_task(self.play_audio())
        except Exception as e:
            print(f"Error: {e}")
            traceback.print_exception(e)
        
        thread.join()
    
def main():
    system_prompt = ''''''
        
    session_properties = OpenAISessionProperties(instructions=system_prompt)
    client = OpenAIWebSocketClient(url, headers, session_properties)
    asyncio.run(client.start())

if __name__ == "__main__":
    main()