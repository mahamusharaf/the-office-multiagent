"""
Train a reward model that predicts action quality based on agent behavior patterns.

This collects episodes from MongoDB and trains a lightweight classifier that learns
which actions tend to get high rewards, enabling:
1. Analysis of agent behavior patterns
2. Potential future reward shaping
3. Ablation studies on prompt effectiveness

FIX: previously trained and evaluated on the exact same data (model.fit(X, y)
then model.score(X, y)), which meant "accuracy" was really just how well the
model memorized its own training set -- with only ~40 samples and 5 features,
a RandomForest can trivially hit 100% that way, telling you nothing about
whether it generalizes. Now uses a held-out test split, so train_accuracy and
test_accuracy are reported separately. Note: with small datasets (well under
100 episodes), the test set is tiny (~10 rows), so test_accuracy will be noisy
-- treat it as a rough signal, not a precise number, until much more data
has accumulated.
"""
import joblib
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import train_test_split
from app.database import training_collection


def extract_features(episode: dict) -> list:
    """
    Convert a training episode into feature vector for the classifier.

    Features:
    - Agent type (vex=0, niblet=1, pim=2, riko=3)
    - Action type (idle=0, post_public_message=1, talk_to_agent=2)
    - Message length
    - Context length (how much activity was happening)
    - Is target present (for talk_to_agent)
    """
    agent_map = {"vex": 0, "niblet": 1, "pim": 2, "riko": 3}
    action_map = {"idle": 0, "post_public_message": 1, "talk_to_agent": 2}

    agent_id = agent_map.get(episode.get("agent", "vex"), 0)
    action_id = action_map.get(episode.get("action", "idle"), 0)

    message_length = len(episode.get("action_content", ""))
    context_length = len(episode.get("world_summary", ""))
    has_target = 1 if episode.get("target") else 0

    return [
        agent_id,
        action_id,
        message_length,
        context_length,
        has_target
    ]


async def train_reward_model():
    """
    Train a RandomForest classifier to predict if an action will have positive reward.

    Returns: {status, train_accuracy, test_accuracy, num_episodes, num_train,
              num_test, model_path, feature_importance}
    """
    # Fetch all episodes from MongoDB
    episodes = await training_collection.find({}).to_list(None)

    if len(episodes) < 10:
        return {
            "status": "insufficient_data",
            "episodes_collected": len(episodes),
            "min_required": 10
        }

    # Extract features and labels
    X = []
    y = []

    for ep in episodes:
        features = extract_features(ep)
        X.append(features)
        # Label: 1 if reward > 0 (good), 0 if reward <= 0 (bad)
        y.append(1 if ep.get("reward", 0) > 0 else 0)

    # Held-out test split so accuracy reflects generalization, not memorization.
    # With very small datasets this split is small too -- interpret cautiously.
    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.25, random_state=42
    )

    # Train lightweight classifier
    model = RandomForestClassifier(
        n_estimators=10,
        max_depth=5,
        random_state=42
    )
    model.fit(X_train, y_train)

    # Report both -- train_accuracy shows fit quality, test_accuracy shows
    # (rough, noisy at this data size) generalization.
    train_accuracy = model.score(X_train, y_train)
    test_accuracy = model.score(X_test, y_test)

    # Save model
    model_path = "reward_model.pkl"
    joblib.dump(model, model_path)

    return {
        "status": "success",
        "train_accuracy": float(train_accuracy),
        "test_accuracy": float(test_accuracy),
        "num_episodes": len(episodes),
        "num_train": len(X_train),
        "num_test": len(X_test),
        "model_path": model_path,
        "feature_importance": {
            "agent": float(model.feature_importances_[0]),
            "action_type": float(model.feature_importances_[1]),
            "message_length": float(model.feature_importances_[2]),
            "context_length": float(model.feature_importances_[3]),
            "has_target": float(model.feature_importances_[4])
        }
    }


async def get_agent_performance(agent_name: str):
    """Analyze an agent's performance across all episodes."""
    episodes = await training_collection.find({"agent": agent_name}).to_list(None)

    if not episodes:
        return {
            "agent": agent_name,
            "episodes": 0,
            "avg_reward": None
        }

    rewards = [ep.get("reward", 0) for ep in episodes]
    avg_reward = sum(rewards) / len(rewards)

    # Count action types
    action_counts = {}
    for ep in episodes:
        action = ep.get("action", "idle")
        action_counts[action] = action_counts.get(action, 0) + 1

    return {
        "agent": agent_name,
        "episodes": len(episodes),
        "avg_reward": float(avg_reward),
        "action_distribution": action_counts,
        "high_reward_episodes": sum(1 for r in rewards if r > 0.5)
    }