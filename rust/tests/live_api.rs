use watasu::{Sandbox, SnapshotListOptions};

#[tokio::test]
async fn live_snapshot_list_shape() {
    if std::env::var("WATASU_LIVE_API_TESTS").ok().as_deref() != Some("1") {
        eprintln!("set WATASU_LIVE_API_TESTS=1 to run live API smoke tests");
        return;
    }

    let page = Sandbox::list_snapshots_page(SnapshotListOptions {
        limit: Some(2),
        ..SnapshotListOptions::default()
    })
    .await
    .expect("live snapshot list request should succeed");

    assert!(page.snapshots.len() <= 2);
}
