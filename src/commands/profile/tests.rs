use super::*;

#[test]
fn parse_friend_profile_cache_hint_extracts_ids_and_access_permit() {
    let hint = parse_profile_cache_hint(
            136,
            "https://katalk.kakao.com/mac/profile3/friend.json?accessPermit=example-access-permit-token&chatId=900000000000002&id=100000002",
            true,
        );

    assert_eq!(hint.kind, "friend");
    assert_eq!(hint.user_ids, vec![100000002]);
    assert_eq!(hint.chat_id, Some(900000000000002));
    assert_eq!(
        hint.access_permit.as_deref(),
        Some("example-access-permit-token")
    );
    assert!(hint.data_on_fs);
}

#[test]
fn parse_friends_profile_cache_hint_extracts_ids_list() {
    let hint = parse_profile_cache_hint(
            88,
            "https://katalk.kakao.com/mac/profile3/friends.json?category=action&ids=%5B100000004%2C100000005%2C100000006%5D",
            false,
        );

    assert_eq!(hint.kind, "friends");
    assert_eq!(hint.user_ids, vec![100000004, 100000005, 100000006]);
    assert_eq!(hint.category.as_deref(), Some("action"));
    assert_eq!(hint.chat_id, None);
    assert_eq!(hint.access_permit, None);
}

#[test]
fn collect_hint_chat_ids_prefers_user_specific_chat_hints() {
    let hints = vec![
        ProfileCacheHint {
            entry_id: 1,
            kind: "friend".into(),
            request_key: String::new(),
            user_ids: vec![100000002],
            chat_id: Some(900000000000002),
            access_permit: Some("permit-a".into()),
            category: None,
            data_on_fs: true,
        },
        ProfileCacheHint {
            entry_id: 2,
            kind: "friend".into(),
            request_key: String::new(),
            user_ids: vec![100000002],
            chat_id: Some(900000000000002),
            access_permit: Some("permit-b".into()),
            category: None,
            data_on_fs: true,
        },
        ProfileCacheHint {
            entry_id: 3,
            kind: "friend".into(),
            request_key: String::new(),
            user_ids: vec![100000003],
            chat_id: Some(900000000000003),
            access_permit: Some("permit-c".into()),
            category: None,
            data_on_fs: true,
        },
    ];

    assert_eq!(
        collect_hint_chat_ids(&hints, 100000002),
        vec![900000000000002]
    );
    assert_eq!(
        collect_hint_chat_ids(&hints, 100000003),
        vec![900000000000003]
    );
    assert!(collect_hint_chat_ids(&hints, 999).is_empty());
}

#[test]
fn local_graph_summary_carries_getmem_tokens() {
    let snapshot = LocalFriendGraphSnapshot {
        user_count: 1,
        chat_count: 1,
        failed_chat_ids: Vec::new(),
        chat_meta: vec![LocalFriendGraphChatMeta {
            chat_id: 900000000000002,
            title: "Example".into(),
            getmem_token: Some(777),
            member_count: 2,
        }],
        entries: vec![LocalFriendGraphEntry {
            user_id: 100000002,
            account_id: 200000001,
            nickname: "Alice".into(),
            country_iso: "KR".into(),
            status_message: String::new(),
            profile_image_url: String::new(),
            full_profile_image_url: String::new(),
            original_profile_image_url: String::new(),
            access_permits: vec!["permit-token".into()],
            suspicion: String::new(),
            suspended: false,
            memorial: false,
            member_type: 0,
            chat_ids: vec![900000000000002],
            chat_titles: vec!["Example".into()],
            is_self: false,
            hidden_like: false,
            hidden_block_type: None,
        }],
    };
    let hints = vec![ProfileCacheHint {
        entry_id: 1,
        kind: "friend".into(),
        request_key: String::new(),
        user_ids: vec![100000002],
        chat_id: Some(900000000000002),
        access_permit: Some("permit-token".into()),
        category: None,
        data_on_fs: true,
    }];

    let summary = local_graph_hint_summary(&snapshot, &hints);
    assert_eq!(summary.candidate_matches.len(), 1);
    assert_eq!(
        summary.candidate_matches[0].candidate_getmem_tokens,
        vec![777]
    );
}

#[test]
fn syncmainpf_candidates_include_getmem_token_fields() {
    let snapshot = LocalFriendGraphSnapshot {
        user_count: 1,
        chat_count: 1,
        failed_chat_ids: Vec::new(),
        chat_meta: vec![LocalFriendGraphChatMeta {
            chat_id: 900000000000002,
            title: "Example".into(),
            getmem_token: Some(777),
            member_count: 2,
        }],
        entries: vec![LocalFriendGraphEntry {
            user_id: 100000002,
            account_id: 200000001,
            nickname: "Alice".into(),
            country_iso: "KR".into(),
            status_message: String::new(),
            profile_image_url: String::new(),
            full_profile_image_url: String::new(),
            original_profile_image_url: String::new(),
            access_permits: vec!["permit-token".into()],
            suspicion: String::new(),
            suspended: false,
            memorial: false,
            member_type: 0,
            chat_ids: vec![900000000000002],
            chat_titles: vec!["Example".into()],
            is_self: false,
            hidden_like: false,
            hidden_block_type: None,
        }],
    };

    let candidate =
        build_syncmainpf_candidate(&snapshot, &[], 100000002).expect("candidate should be built");

    assert_eq!(candidate.getmem_tokens, vec![777]);
    assert!(candidate
        .bodies
        .iter()
        .any(|body| body.get("token").and_then(|v| v.as_i64()) == Some(777)));
    assert!(candidate
        .bodies
        .iter()
        .any(|body| body.get("profileToken").and_then(|v| v.as_i64()) == Some(777)));
    assert!(candidate
        .uplinkprof_bodies
        .iter()
        .any(|body| body.get("token").and_then(|v| v.as_i64()) == Some(777)));
}
