"""Read a stream's real resolution out of the media itself.

The scanner marks a verified stream whose resolution it could not read as
quality_unknown, and the Pages validator honours that so working Bangladeshi
channels survive. But "unknown" is the absence of an answer, and asking the
stream produced one for 110 of the 120 cards carrying it: 75 were at or above
720p and had simply never declared it, 35 were genuinely below the floor and
were being published under a blanket exemption, and 10 could not be read at
all.

Two kinds of evidence, both of them measurement rather than assertion:

  * an HLS master playlist's RESOLUTION attribute
  * the H.264 SPS decoded from transport-stream bytes, which also says whether
    the picture is interlaced - the property that explains why Zee Bangla's old
    1080i route decoded nowhere

Extracted from scripts/resolve-unknown-resolutions.py so the verifier and the
audit script cannot disagree about what a stream is.
"""
from __future__ import annotations

import re
from typing import Any, Dict, Optional

#: Below this, a decoded height is an artefact rather than a picture. Real SPS
#: decodes in this project produced 8 and 16 on two malformed streams, and a
#: television channel is never either.
MINIMUM_PLAUSIBLE_HEIGHT = 120


class _Bits:
    """Just enough bit reading for an H.264 SPS."""

    def __init__(self, data: bytes) -> None:
        self.data = data
        self.pos = 0

    def bit(self) -> int:
        index, offset = divmod(self.pos, 8)
        if index >= len(self.data):
            raise EOFError
        self.pos += 1
        return (self.data[index] >> (7 - offset)) & 1

    def bits(self, count: int) -> int:
        value = 0
        for _ in range(count):
            value = (value << 1) | self.bit()
        return value

    def ue(self) -> int:
        zeros = 0
        while self.bit() == 0:
            zeros += 1
            if zeros > 32:
                raise EOFError
        if zeros == 0:
            return 0
        return (1 << zeros) - 1 + self.bits(zeros)

    def se(self) -> int:
        value = self.ue()
        return (value + 1) // 2 if value % 2 else -(value // 2)


def _unescape(payload: bytes) -> bytes:
    """Remove emulation-prevention bytes."""
    out = bytearray()
    zeros = 0
    for byte in payload:
        if zeros >= 2 and byte == 0x03:
            zeros = 0
            continue
        out.append(byte)
        zeros = zeros + 1 if byte == 0x00 else 0
    return bytes(out)


def decode_sps(payload: bytes) -> Optional[Dict[str, Any]]:
    """Width, height and scan type from an H.264 SPS NAL payload."""
    try:
        bits = _Bits(_unescape(payload))
        profile_idc = bits.bits(8)
        bits.bits(8)  # constraint flags + reserved
        level_idc = bits.bits(8)
        bits.ue()  # seq_parameter_set_id
        chroma_format_idc = 1
        if profile_idc in (100, 110, 122, 244, 44, 83, 86, 118, 128, 138, 139, 134, 135):
            chroma_format_idc = bits.ue()
            if chroma_format_idc == 3:
                bits.bit()
            bits.ue()  # bit_depth_luma_minus8
            bits.ue()  # bit_depth_chroma_minus8
            bits.bit()  # qpprime_y_zero_transform_bypass_flag
            if bits.bit():  # seq_scaling_matrix_present_flag
                count = 8 if chroma_format_idc != 3 else 12
                for index in range(count):
                    if bits.bit():
                        size = 16 if index < 6 else 64
                        last = next_scale = 8
                        for _ in range(size):
                            if next_scale != 0:
                                delta = bits.se()
                                next_scale = (last + delta + 256) % 256
                            last = next_scale or last
        bits.ue()  # log2_max_frame_num_minus4
        pic_order_cnt_type = bits.ue()
        if pic_order_cnt_type == 0:
            bits.ue()
        elif pic_order_cnt_type == 1:
            bits.bit()
            bits.se()
            bits.se()
            for _ in range(bits.ue()):
                bits.se()
        bits.ue()  # max_num_ref_frames
        bits.bit()  # gaps_in_frame_num_value_allowed_flag
        width_mbs = bits.ue() + 1
        height_map_units = bits.ue() + 1
        frame_mbs_only = bits.bit()
        if not frame_mbs_only:
            bits.bit()  # mb_adaptive_frame_field_flag
        bits.bit()  # direct_8x8_inference_flag
        crop_left = crop_right = crop_top = crop_bottom = 0
        if bits.bit():  # frame_cropping_flag
            crop_left = bits.ue()
            crop_right = bits.ue()
            crop_top = bits.ue()
            crop_bottom = bits.ue()

        sub_width = 2 if chroma_format_idc in (1, 2) else 1
        sub_height = 2 if chroma_format_idc == 1 else 1
        width = width_mbs * 16 - (crop_left + crop_right) * sub_width
        height = (
            (2 - frame_mbs_only) * height_map_units * 16
            - (crop_top + crop_bottom) * sub_height * (2 - frame_mbs_only)
        )
        if not (0 < width <= 8192 and 0 < height <= 8192):
            return None
        if height < MINIMUM_PLAUSIBLE_HEIGHT:
            # Two real streams decoded to 8 and 16 pixels. That is a malformed
            # SPS, not a channel, and publishing it as a measured resolution
            # would be worse than admitting the resolution is unknown.
            return None
        return {
            "profile_idc": profile_idc,
            "level_idc": level_idc,
            "width": width,
            "height": height,
            "frame_mbs_only_flag": frame_mbs_only,
            "scan_type": "progressive" if frame_mbs_only else "interlaced",
        }
    except (EOFError, IndexError, ValueError):
        return None


def sps_from_transport_stream(data: bytes) -> Optional[Dict[str, Any]]:
    """Find an SPS in MPEG-TS payload bytes and decode it."""
    for match in re.finditer(b"\x00\x00\x01", data):
        start = match.end()
        if start >= len(data):
            break
        if (data[start] & 0x1F) != 7:  # nal_unit_type 7 == SPS
            continue
        end = data.find(b"\x00\x00\x01", start)
        payload = data[start + 1: end if end > start else start + 200]
        decoded = decode_sps(payload)
        if decoded:
            return decoded
    return None


def master_playlist_height(text: Any) -> int:
    """Tallest RESOLUTION an HLS master declares, or 0."""
    heights = [
        int(match.group(2))
        for match in re.finditer(r"RESOLUTION=(\d+)x(\d+)", str(text or ""))
    ]
    heights = [h for h in heights if h >= MINIMUM_PLAUSIBLE_HEIGHT]
    return max(heights) if heights else 0


def plausible(height: Any) -> int:
    """The height if it could be a picture, else 0."""
    try:
        value = int(height or 0)
    except (TypeError, ValueError):
        return 0
    return value if value >= MINIMUM_PLAUSIBLE_HEIGHT else 0
