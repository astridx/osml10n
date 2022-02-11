#!/usr/bin/python3

import asyncio
import struct
import sys

import argparse
# as imports are very slow parse arguments first
parser = argparse.ArgumentParser(description='Server for transcription of names based on geolocation')
parser.add_argument("-b", "--bindaddr", type=str, default="localhost", help="local bind address")
parser.add_argument("-p", "--port", default=8033, help="port to listen at")
parser.add_argument("-v", "--verbose", action='store_true', help="print verbose output")
group = parser.add_mutually_exclusive_group()
group.add_argument('-d', '--dbcon', help='PostgreSQL (psycopg2) connection string')
group.add_argument('-s', '--sqlitefile', default='country_osm_grid.db', help='SQLITE file')

args = parser.parse_args()

def vout(msg):
  if args.verbose:
    sys.stdout.write(msg)
    sys.stdout.flush()

sys.stdout.write("Loading osml10n transcription server: ")
sys.stdout.flush()
vout("\n")

from contextlib import contextmanager,redirect_stderr,redirect_stdout
from os import devnull

import os
import icu
import unicodedata
# Kanji in JP
import pykakasi
# thai language in TH
import tltk
# Cantonese transcription
with open(devnull, 'w') as fnull:
  with redirect_stderr(fnull) as err, redirect_stdout(fnull) as out:
    import pinyin_jyutping_sentence

def split_by_alphabet(str):
    strlist=[]
    target=''
    oldalphabet=unicodedata.name(str[0]).split(' ')[0]
    target=str[0]
    for c in str[1:]:
      alphabet=unicodedata.name(c).split(' ')[0]
      if (alphabet==oldalphabet):
        target=target+c
      else:
        strlist.append(target)
        target=c
      oldalphabet=alphabet
    strlist.append(target)
    return(strlist)

def thai_transcript(inpstr):
  stlist=split_by_alphabet(inpstr)

  latin = ''
  for st in stlist:
    if (unicodedata.name(st[0]).split(' ')[0] == 'THAI'):
      transcript=''
      try:
        transcript=tltk.nlp.th2roman(st).rstrip('<s/>').rstrip()
      except:
        sys.stderr.write("tltk error transcribing >%s<\n" % st)
        return(None)
      latin=latin+transcript
    else:
      latin=latin+st
  return(latin)

def cantonese_transcript(inpstr):
  stlist=split_by_alphabet(inpstr)

  latin = ''
  for st in stlist:
    if (unicodedata.name(st[0]).split(' ')[0] == 'CJK'):
      transcript=''
      try:
        transcript=pinyin_jyutping_sentence.jyutping(st, spaces=True)
      except:
        sys.stderr.write("pinyin_jyutping_sentence error transcribing >%s<\n" % st)
        return(None)
      latin=latin+transcript
    else:
      latin=latin+st
  return(latin)

# helper function "contains_thai"
# checks if string contains Thai language characters
# 0x0400-0x04FF in unicode table
def contains_thai(text):
  for c in text:
    if (ord(c) > 0x0E00) and (ord(c) < 0x0E7F):
      return True
  return False

# helper function "contains_cjk"
# checks if string contains CJK characters
# 0x4e00-0x9FFF in unicode table
def contains_cjk(text):
  for c in text:
    if (ord(c) > 0x4e00) and (ord(c) < 0x9FFF):
      return True
  return False

class transcriptor:
  def __init__(self):

    # ICU transliteration instance
    self.icutr = icu.Transliterator.createInstance('Any-Latin').transliterate

    # Kanji to Latin transcription instance via pykakasi
    self.kakasi = pykakasi.kakasi()

  def transcript(self, country, unistr):
    if (country == ""):
      vout("doing non-country specific transcription for >>%s<<\n" % unistr)
    else:
      vout("doing transcription for >>%s<< (country %s)\n" % (unistr,country))
    if country == 'jp':
      # this should mimic the old api behavior (I hate API changes)
      # new API does not have all options anymore :(
      kanji = self.kakasi.convert(unistr)
      out = ""
      for w in kanji:
        w['hepburn'] = w['hepburn'].strip()
        if (len(w['hepburn']) > 0):
          out = out +  w['hepburn'].capitalize() + " "
      return(out.strip())

    if country == 'th':
      return(thai_transcript(unistr))

    if country in ['mo','hk']:
      return(cantonese_transcript(unistr))

    return(unicodedata.normalize('NFC', self.icutr(unistr)))

# convert lon/lat to countrycode via PostgreSQL
class Coord2Country_psql:
  def __init__(self):
      import psycopg2
      self.sql = """
      SELECT country_code from country_osm_grid
      WHERE st_contains(geometry, ST_GeomFromText('POINT(%s %s)', 4326))
      ORDER BY area LIMIT 1;
      """
      try:
        self.conn = psycopg2.connect(args.dbcon)
      except:
        sys.stderr.write("Unable to connect to database using %s " % args.dbcon)
        sys.stderr.write("falling back to countrycode-only mode\n")
        self.ready = False
        return
      self.cur = self.conn.cursor()
      self.ready = True
  def getCountry(self,lon,lat):
    try:
      self.cur.execute(self.sql % (lon,lat))
      rows = self.cur.fetchall()
      if len(rows) == 0:
        return('')
      else:
        return(rows[0][0])
    except Exception as e:
      sys.stderr.write("Database query error:\n")
      sys.stderr.write(str(e))
      sys.exit(1)

# convert lon/lat to countrycode via SQLITE
class Coord2Country_sqlite:
  def __init__(self):
    # check if sqlite file is available
    fn = os.path.realpath(args.sqlitefile)
    if not os.path.isfile(fn):
      sys.stderr.write("Unable to open SQLITE file %s, " % args.sqlitefile)
      sys.stderr.write("falling back to countrycode-only mode\n")
      self.ready = False
      return
    import sqlite3
    self.sql = """
    SELECT country_code
    FROM country_osm_grid
    WHERE st_contains(geometry, ST_GeomFromText('POINT(%s %s)', 4326))
    AND ROWID IN (
      SELECT ROWID
      FROM SpatialIndex
      WHERE f_table_name = 'country_osm_grid'
      AND search_frame = ST_GeomFromText('POINT(%s %s)', 4326)
    ) ORDER BY area LIMIT 1;
    """
    self.conn = sqlite3.connect(fn)
    self.conn.enable_load_extension(True)
    self.conn.load_extension("mod_spatialite")
    self.cur = self.conn.cursor()
    self.ready = True

  def getCountry(self,lon,lat):
    self.cur.execute(self.sql % (lon,lat,lon,lat))
    rows = self.cur.fetchall()
    if len(rows) == 0:
      return('')
    else:
      return(rows[0][0])

# convert lon/lat to countrycode via PostgreSQL
class Coord2Country:
  def __init__(self):
    if args.dbcon is not None:
      vout("Using PostgreSQL for country_osm_grid!\n")
      self.co2c = Coord2Country_psql()
    else:
      vout("Using SQLITE for country_osm_grid!\n")
      self.co2c = Coord2Country_sqlite()
    self.ready = self.co2c.ready
  def getCountry(self,lon,lat):
    # if no coordinates are given
    if (lat == "") and (lon == ""):
      return('')
    country = self.co2c.getCountry(lon,lat)
    if (country == ""):
      vout("country for %s/%s is unknown\n" % (lon,lat))
    else:
      vout("country for %s/%s is %s\n" % (lon,lat,country))
    return(country)

co2c = Coord2Country()
tc = transcriptor()

# Read a request from the socket. First read 4 bytes containing the length
# of the request data, then read the data itself and return as a UTF-8 string.
# Return 'None' if the connection was closed.
async def read_request(reader):
  try:
    lendata = await reader.readexactly(4)
    if len(lendata) == 0:
      return
    length = struct.unpack('I', lendata)
    if length == 0:
      return
    data = await reader.readexactly(length[0])
    return data.decode('utf-8')
  except asyncio.exceptions.IncompleteReadError:
    return

# Write the reply data to the socket and flush. First writes 4 bytes containing
# the length of the data and then the data itself.
async def send_reply(writer, reply):
  data = reply.encode('utf-8')
  length = len(data)
  writer.write(struct.pack('I', length) + data)
  await writer.drain()

async def handle_connection(reader, writer):
    vout('New connection\n')
    while True:
      data = await read_request(reader)
      if data is None:
        vout('Connection closed\n')
        return

      # We support the following formats:
      # id/cc/string
      # id/lon/lat/string
      qs = data.split('/',3)
      if len(qs) == 3:
        (id,cc,name) = qs
      else:
        (id,lon,lat,name) = qs
        # Do check for country only if string contains Thai or CJK characters
        if co2c.ready:
          if contains_cjk(name):
            cc = co2c.getCountry(lon,lat)
          else:
            if contains_thai(name):
              cc = 'th'
            else:
              cc = ''
        else:
          cc = ''

      try:
        if name != '':
          reply = tc.transcript(cc,name)
        else:
          reply = ''

        if isinstance(reply, str):
          await send_reply(writer, reply)
        else:
          sys.stderr.write(f"Error in id '{id}': transcript('{cc}','{name}') returned non-string '{reply}'\n")
          await send_reply(writer, '')
      except BaseException as err:
        sys.stderr.write(f"Error in id '{id}': {err}, {type(err)}\n")
        await send_reply(writer, '')

async def main():
  server = await asyncio.start_server(handle_connection, host=args.bindaddr, port=args.port, reuse_address=True, reuse_port=True)
  addrs = ', '.join(str(sock.getsockname()) for sock in server.sockets)
  vout(f'Serving on {addrs}\n')

  async with server:
    await server.serve_forever()

if __name__ == "__main__":
  sys.stdout.write("ready.\n")
  asyncio.run(main())

