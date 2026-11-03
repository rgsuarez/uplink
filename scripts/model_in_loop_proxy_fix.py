"""Run all 21 frozen trials with balanced descriptions and the prior system prompt."""

import model_in_loop_sim as driver

from offline_agent.uplink import model_sim_proxy_profile

if __name__ == "__main__":
    driver.top10_model = model_sim_proxy_profile
    driver.main()
