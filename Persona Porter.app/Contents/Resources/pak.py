"""Bounded reader and same-size member replacement for observed P3 PAKs.

These are 256-byte name/size headers with payloads aligned to 64 bytes.
Member names are archive identifiers only, never filesystem extraction paths.
"""
import struct

from persona_porter import FormatError


def entries(data):
    result = []
    seen = set()
    pos = 0
    while pos < len(data):
        if pos + 256 > len(data):
            raise FormatError('Truncated PAK member header')
        header = data[pos:pos + 256]
        if header == bytes(256):
            if any(data[pos:]):
                raise FormatError('Nonzero data after PAK terminator')
            return result
        name_field = header[:252]
        if b'\0' not in name_field:
            raise FormatError('PAK member name has no terminator')
        name, unused = name_field.split(b'\0', 1)
        if not name or any(unused):
            raise FormatError('Invalid PAK member name field')
        try:
            name = name.decode('ascii')
        except UnicodeDecodeError as exc:
            raise FormatError('Non-ASCII PAK member name') from exc
        if name.casefold() in seen:
            raise FormatError('Duplicate PAK member name')
        seen.add(name.casefold())
        size = struct.unpack_from('<I', header, 252)[0]
        offset = pos + 256
        end = offset + size
        if end > len(data):
            raise FormatError('PAK member payload exceeds container')
        result.append({'name': name, 'header_offset': pos, 'offset': offset, 'size': size})
        pos = (end + 63) // 64 * 64
        if pos > len(data):
            raise FormatError('PAK alignment extends past container')
        # Retail bundle padding can contain nonzero bytes. Preserve it verbatim.
    return result


def replace_member(data, name, expected, replacement):
    matches = [e for e in entries(data) if e['name'].casefold() == name.casefold()]
    if len(matches) != 1:
        raise FormatError(f'No unique PAK member matching {name!r}')
    member = matches[0]
    offset, size = member['offset'], member['size']
    if data[offset:offset + size] != expected:
        raise FormatError('Packed lore differs from the expected standalone original')
    if len(replacement) != size:
        raise FormatError('PAK member replacement must preserve its exact original size')
    result = data[:offset] + replacement + data[offset + size:]
    if entries(result) != entries(data):
        raise FormatError('PAK member index changed unexpectedly')
    return result, member
