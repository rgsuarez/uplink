"""Run the existing simulation with the explicit snapshot-authorization prompt.

Only the model-input provider changes; the original driver, fault population,
grammar, parser, independent verification and cleanup paths remain unchanged.
"""

import model_in_loop_sim as driver

from offline_agent.uplink import model_sim_profile

if __name__ == "__main__":
    driver.top10_model = model_sim_profile
    driver.main()
