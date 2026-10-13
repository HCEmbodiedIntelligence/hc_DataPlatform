import base64
import hashlib
import json
import struct
import zlib

import numpy as np
import pytest
from mcap.reader import make_reader
from mcap.writer import Writer

from hc_data_platform.data_formats.holobrain_depth import DEFAULT_PROFILE, DepthVideo, quantize
from hc_data_platform.continuous_recordings.openarm_raw.prepare import prepare
from hc_data_platform.robot_ingest.recording_contract import OpenArmRecordingComplete
from tests.continuous_recordings.test_openarm_raw import declared_synthetic_fixture


def compact_fixture(root):
    declared_synthetic_fixture(root, exposure='measured')
    original=root/'raw.mcap'
    with original.open('rb') as f:
        messages=list(make_reader(f).iter_messages(log_time_order=False))
    videos={}
    refs=[]
    with original.open('wb') as f:
        writer=Writer(f);writer.start()
        channels={}
        for _,channel,message in messages:
            topic,encoding,data=channel.topic,channel.message_encoding,message.data
            if encoding=='humanoid-depth':
                length=struct.unpack('<I',data[:4])[0]
                metadata=json.loads(data[4:4+length]);meta=metadata['depth']
                ident=metadata['source_id'];relative=f'videos/{ident}_depth_source.mp4'
                array=np.frombuffer(data[4+length:],dtype=meta['dtype']).reshape(meta['shape'])
                result=quantize(array,meta['depth_scale'],invalid_values=meta['source_profile']['invalid_values'])
                if ident not in videos:videos[ident]=DepthVideo(root/relative,30,array.shape)
                video=videos[ident]
                reference={'schema_version':'compact-depth-frame/v1',
                    'video_segment_path':relative,'display_frame_index':video.frames,
                    'pts':video.frames,'time_base':[1,30],
                    'profile':DEFAULT_PROFILE.document(),'profile_sha256':DEFAULT_PROFILE.sha256,
                    'source_dtype':array.dtype.str,
                    'codes_sha256':hashlib.sha256(result.codes.astype('<u2').tobytes()).hexdigest(),
                    'flags_zlib_base64':base64.b64encode(zlib.compress(result.flags.tobytes())).decode(),
                    'quantization_report':result.error_report()}
                video.write_codes(result.codes)
                metadata['depth_video']=reference;refs.append((result.codes,reference,meta))
                topic='/humanoid/session/depth_video_frame/'+ident;encoding='json'
                data=json.dumps(metadata).encode()
            if topic not in channels:channels[topic]=writer.register_channel(topic,encoding,0)
            writer.add_message(channels[topic],message.log_time,data,message.publish_time,message.sequence)
        writer.finish()
    for video in videos.values():video.close()
    manifest=json.loads((root/'manifest.json').read_text())
    for ident in videos:
        manifest['committed'].append({'path':f'videos/{ident}_depth_source.mp4','kind':'depth_mp4','closed':True,'fsynced':True})
    (root/'manifest.json').write_text(json.dumps(manifest))
    marker=json.loads((root/'recording-complete.json').read_text())
    from hc_data_platform.object_storage.checksums import crc64_ecma
    inventory=[]
    for path in sorted(root.rglob('*')):
        if not path.is_file() or path.name=='recording-complete.json':continue
        body=path.read_bytes()
        inventory.append({'path':path.relative_to(root).as_posix(),'role':'AUXILIARY','camera_id':None,
            'media_type':'video/mp4' if path.suffix=='.mp4' else 'application/json',
            'size':len(body),'sha256':hashlib.sha256(body).hexdigest(),'crc64':crc64_ecma(body),'part_count':1})
    marker['recording_upload']['assets']=inventory
    (root/'recording-complete.json').write_text(json.dumps(marker))
    return refs


def test_compact_depth_is_accepted_and_preserves_quantization_evidence(tmp_path):
    root=tmp_path/'input';compact_fixture(root)
    marker=OpenArmRecordingComplete.model_validate(json.loads((root/'recording-complete.json').read_text()))
    config,roles=prepare(root,tmp_path/'prepared',marker.recording_upload)
    assert len(config['cameras'])==6
    assert any('depth' in path for path in roles)
    import pyarrow.parquet as pq
    rows=pq.read_table(tmp_path/'prepared/hc/depth/camera_left/frames.parquet').to_pylist()
    assert rows and any(not r['missing'] for r in rows)
    assert all(r['half_cell_bound_passed'] for r in rows)


def test_compact_depth_rejects_changed_pixels_and_mask(tmp_path):
    from hc_data_platform.data_formats.compact_depth import validate_encoded_depth
    codes,ref,meta=compact_fixture(tmp_path/'input')[0]
    altered=codes.copy();altered[0,0]=(int(altered[0,0])+1)%4096
    with pytest.raises(ValueError,match='hash'):
        validate_encoded_depth(altered,ref,meta['source_profile'],meta['shape'])
    broken={**ref,'flags_zlib_base64':base64.b64encode(zlib.compress(b'bad')).decode()}
    with pytest.raises(ValueError,match='flags size'):
        validate_encoded_depth(codes,broken,meta['source_profile'],meta['shape'])
