//! The engine of a session with no model yet (`Protocol::Waiting`): the inference lists none for
//! the person. It runs nothing: it says it is ready with the models the spec lists (the plans the
//! harness's client signs in to, when there are), switches by restarting, and leaves the rest to
//! the screen, which keeps prompts back until a model is chosen and lists the inference's models
//! afresh each time /model opens. Choosing one starts the harness on it through the spec's
//! `respec`, as any switch that restarts does.
use crate::protocol::{Command, EngineHandle, Event, ModelSwitch, Ready};
use crate::spec::EngineSpec;

pub fn start(spec: &EngineSpec) -> EngineHandle {
    let (handle, mut commands, events) = EngineHandle::pair();
    let ready = Ready {
        harness: spec.harness.clone(),
        name: spec.name.clone(),
        model: String::new(),
        models: spec.models.clone(),
        model_switch: ModelSwitch::Restart,
        session: None,
        info: Some(format!("{} starts once you choose a model", spec.name)),
    };
    tokio::spawn(async move {
        if events.send(Event::Ready(ready)).is_err() {
            return;
        }
        while let Some(command) = commands.recv().await {
            if matches!(command, Command::Shutdown) {
                return;
            }
        }
    });
    handle
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn says_it_is_ready_with_no_model_and_switches_by_restarting() {
        let spec: EngineSpec = serde_json::from_str(
            r#"{"harness":"codex","name":"Codex CLI","protocol":"waiting","cwd":"/workspace","model":"",
                "models":["subscription:chatgpt/default"],"respec":["python3","/opt/solver/runtime.py","engine","codex"],
                "state_dir":"/s"}"#,
        )
        .unwrap();
        spec.check().unwrap();
        let mut engine = start(&spec);
        let Some(Event::Ready(ready)) = engine.events.recv().await else { panic!("not ready") };
        assert_eq!((ready.model.as_str(), ready.model_switch), ("", ModelSwitch::Restart));
        assert_eq!(ready.models, vec!["subscription:chatgpt/default".to_owned()]);
        engine.commands.send(Command::Shutdown).unwrap();
        assert!(engine.events.recv().await.is_none(), "it ends on Shutdown");
    }

    #[test]
    fn a_waiting_spec_has_no_model_and_a_respec() {
        let with_model: EngineSpec = serde_json::from_str(
            r#"{"harness":"codex","name":"Codex CLI","protocol":"waiting","cwd":"/w","model":"m","respec":["x"],"state_dir":"/s"}"#,
        )
        .unwrap();
        assert!(with_model.check().is_err());
        let no_respec: EngineSpec =
            serde_json::from_str(r#"{"harness":"codex","name":"Codex CLI","protocol":"waiting","cwd":"/w","model":"","state_dir":"/s"}"#)
                .unwrap();
        assert!(no_respec.check().is_err());
        let other: EngineSpec =
            serde_json::from_str(r#"{"harness":"codex","name":"Codex CLI","protocol":"acp","argv":["a"],"cwd":"/w","model":"","state_dir":"/s"}"#)
                .unwrap();
        assert!(other.check().is_err(), "only a waiting spec goes without a model");
    }
}
