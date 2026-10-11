import {describe,it,expect} from 'vitest';
import {generationDetail} from '../generation-detail';

describe('generation evidence display',()=>{
  it('does not turn a requested model into an observed response',()=>{
    const lines=generationDetail({schema_version:1,selection:{model_id:'selected/model'}});
    expect(lines).toEqual(['요청 모델 · selected/model · 응답 모델 미확인']);
  });
  it('shows observed model/time/zero usage and bounded image coverage',()=>{
    const lines=generationDetail({schema_version:1,selection:{model_id:'selected/model'},
      response:{model_id:'observed/model',usage:{total_tokens:0}},
      timing:{elapsed_ms:1200,elapsed_scope:'routed_generation_call'},
      image_context:{selected:[{origin:'current'},{origin:'previous'}],omitted:{byte_budget:1}}});
    expect(lines).toEqual(['응답 모델 · observed/model','응답 처리 · 1.20초','관측 사용량 · 0 토큰',
      '전달한 사진 · 이번 1장 · 이전 1장','사진 1장 제외 · 한도 또는 접근 실패']);
  });
  it('keeps unknown counts/times absent and does not render credentials or URLs',()=>{
    const lines=generationDetail({schema_version:1,selection:{model_id:'sk-SECRET'},response:{model_id:'https://secret.invalid',usage:{total_tokens:-1}},
      timing:{elapsed_ms:Infinity,elapsed_scope:'routed_generation_call'},token:'PRIVATE',backend_id:'SECRET'});
    expect(lines).toEqual([]);
    expect(generationDetail({schema_version:2,response:{model_id:'unverified/model'}})).toEqual([]);
  });
});
