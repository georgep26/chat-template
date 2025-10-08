# Jupyter Notebooks

This directory contains Jupyter notebooks for experimentation, data analysis, and prototyping.

## Getting Started

1. Activate the conda environment:
   ```bash
   conda activate project-env
   ```

2. Install Jupyter if not already installed:
   ```bash
   conda install jupyter
   ```

3. Start Jupyter Lab:
   ```bash
   jupyter lab
   ```

   Or start Jupyter Notebook:
   ```bash
   jupyter notebook
   ```

## Notebook Guidelines

- Use descriptive names for notebooks (e.g., `data_exploration.ipynb`)
- Include markdown cells to explain your analysis
- Keep notebooks focused on specific tasks
- Clean up outputs before committing to version control
- Consider converting useful notebooks to Python scripts in the `src/` directory

## Example Notebooks

- `01_data_exploration.ipynb` - Initial data analysis and exploration
- `02_model_development.ipynb` - Model training and evaluation
- `03_visualization.ipynb` - Data visualization and reporting

## Environment

The notebooks use the same conda environment as the main project (`project-env`), ensuring consistency between experimentation and production code.
