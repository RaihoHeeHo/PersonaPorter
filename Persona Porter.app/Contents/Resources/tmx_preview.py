#!/usr/bin/env python3
"""Read-only PNG export of the single-palette PSMT8 TMX files used here."""
import argparse
from pathlib import Path
import struct
import zlib

from persona_porter import FormatError


def tmx_info(data):
    if len(data) < 64 or data[8:12] != b'TMX0':
        raise FormatError('Not a TMX0 texture')
    declared = struct.unpack_from('<I', data, 4)[0]
    palettes, palette_format, width, height, pixel_format, mipmaps = struct.unpack_from('<BBHHBB', data, 16)
    if declared != len(data):
        raise FormatError('TMX declared size differs from file size')
    if (palettes, palette_format, pixel_format, mipmaps) != (1, 0, 0x13, 0):
        raise FormatError('Only one RGBA32 palette, PSMT8 pixels and no mipmaps are supported')
    if not width or not height or len(data) != 64 + 1024 + width * height:
        raise FormatError('Unexpected TMX dimensions or payload length')
    return {'width': width, 'height': height, 'pixel_format': pixel_format,
            'palette_format': palette_format, 'palette_count': palettes, 'mipmaps': mipmaps}


def png_bytes(data):
    info = tmx_info(data)
    palette = []
    for i in range(256):
        # PS2 CSM1 palette layout exchanges index bits 3 and 4.
        j = (i & ~0x18) | ((i & 8) << 1) | ((i & 16) >> 1)
        r, g, b, a = data[64 + j * 4:68 + j * 4]
        palette.append(bytes((r, g, b, min(255, a * 255 // 128))))
    indices = data[1088:]
    width, height = info['width'], info['height']
    pixels = b''.join(b'\0' + b''.join(palette[i] for i in indices[y * width:(y + 1) * width])
                      for y in range(height))
    def chunk(kind, payload):
        return struct.pack('>I', len(payload)) + kind + payload + struct.pack('>I', zlib.crc32(kind + payload))
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 6, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(pixels)) + chunk(b'IEND', b''))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    try:
        png = png_bytes(args.input.read_bytes())
        with args.output.open('xb') as f:
            f.write(png)
    except (OSError, FormatError) as exc:
        parser.exit(2, f'Error: {exc}\n')
